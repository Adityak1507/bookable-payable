"""The line-item table: header roles, row roles confirmed by arithmetic, rate columns, header-less fallbacks."""
from __future__ import annotations

import re
from decimal import Decimal
from itertools import permutations

from .. import lexicon as lx
from ..ir import Line
from ..numbers import money, near, parse_amount
from ..ocr.layout import TextLine
from .cells import HUNDRED, Cell, cells_of, _is_num, num, _QTY_UNIT, _ORD_DEL, _QTY_TIMES, row_numbers, is_code
from .page import PageInfo
from .summary import _summary_label
from ..rules import rule


_ROLE_LISTS = {"qty": lx.COL_QTY, "price": lx.COL_PRICE, "total": lx.COL_TOTAL, "disc": lx.COL_DISC,
               "rate": lx.COL_RATE, "desc": lx.COL_DESC, "uom": lx.COL_UOM}


def _subcells(cells: list[Cell]) -> list[Cell]:
    """OCR often boxes several column titles together ('Summa KM%'). Split such boxes into
    word n-grams with x-extents proportional to character offsets."""
    out: list[Cell] = []
    for c in cells:
        toks = c.text.split()
        if len(toks) <= 1:
            out.append(c)
            continue
        width = (c.x1 - c.x0) / max(len(c.text), 1)
        pos, spans = 0, []
        for t in toks:
            start = c.text.index(t, pos)
            spans.append((t, c.x0 + start * width, c.x0 + (start + len(t)) * width))
            pos = start + len(t)
        for n in (3, 2, 1):
            if n >= len(spans):
                continue                       # the whole box is matched separately, unsplit
            for k in range(len(spans) - n + 1):
                grp = spans[k:k + n]
                out.append(Cell(" ".join(t for t, _, _ in grp), grp[0][1], grp[-1][2]))
    return out


def _header_roles(cells: list[Cell]) -> dict[str, Cell]:
    roles: dict[str, Cell] = {}
    whole = _header_roles_on(cells)
    split = _header_roles_on(_subcells(cells))
    roles = split if len(split) > len(whole) else whole
    return roles


@rule("items.excl_column_preferred", "Given excluding- and including-tax columns, the excluding one is the net the ERP wants.")
def _header_roles_on(cells: list[Cell]) -> dict[str, Cell]:
    roles: dict[str, Cell] = {}
    claimed: list[Cell] = []
    for c in sorted(cells, key=lambda c: -len(c.text)):
        if any(min(c.x1, d.x1) - max(c.x0, d.x0) > 0.5 * (c.x1 - c.x0) for d in claimed):
            continue
        f = re.sub(r"[^a-z% ]+", " ", lx.fold(c.text)).strip()
        f = re.sub(r"\s+", " ", re.sub(r"([a-z])%", r"\1 %", f))
        if not f:
            continue
        for role in ("price", "qty", "disc", "rate", "total", "uom", "desc"):
            if any(f == p or f.startswith(p + " ") or f.endswith(" " + p) for p in _ROLE_LISTS[role]):
                prev = roles.get(role)
                # with both 'Price excl.' and 'Price incl.', the net (excluding-tax) column is the
                # one the ERP wants; the including-tax column restates it
                if prev is None or (lx.has(c.text, lx.EXCL_HINT) and lx.has(prev.text, lx.INCL_HINT)):
                    if prev is not None:
                        claimed.remove(prev)
                    roles[role] = c
                    claimed.append(c)
                    break
                if role in roles:
                    continue
    return roles


@rule("items.header_describes_items", "An item table header names a description, quantity or price column.")
def _is_header(cells: list[Cell]) -> bool:
    roles = _header_roles(cells)
    return (len(roles) >= 2 and ("total" in roles or "price" in roles)
            and bool({"desc", "qty", "price"} & roles.keys())          # an item table describes items
            and sum(_is_num(c.text) for c in cells) <= 1)


@rule("items.row_closes", "Column roles are confirmed by qty x price = total; among closing readings the most plausible wins.")
@rule("items.price_unit", "qty x price / PU = total, with the price unit printed on the row.")
@rule("items.discount_value", "A discount printed as % and value is proven by gross x % = value; the net is gross - value.")
@rule("items.set_component", "A price under the price column with nothing under the amount column is a set component, not billed.")
@rule("items.units_not_amounts", "A value carrying a unit (KG, PC) is never a price or an amount.")
def _row_from_numbers(desc: str, nums: list[tuple[Cell, Decimal]], roles: dict[str, Cell],
                      pct_cells: set[int]) -> tuple[Line, bool, list[tuple[Cell, Decimal]]]:
    """Assign roles to a row's numbers: header columns propose, qty × price ≈ total confirms.
    Returns the line, whether its arithmetic closes, and the numbers left unassigned (each
    printed number is used for at most one role)."""
    li = Line(description=desc.strip())
    vals = [v for _, v in nums]
    cells = [c for c, _ in nums]
    by_col: dict[str, int] = {}
    unit_cells = {n for n in range(len(nums)) if _QTY_UNIT.match(cells[n].text.strip())
                  or _ORD_DEL.match(cells[n].text.strip()) or _QTY_TIMES.match(cells[n].text.strip())}
    for role, hc in roles.items():
        if role in ("desc", "uom", "rate"):
            continue
        pool = [n for n in range(len(nums)) if role == "qty" or n not in unit_cells]   # '0,004 KG' is no price
        n = min(pool, key=lambda n: abs(cells[n].xc - hc.xc), default=None)
        if n is not None and cells[n].x0 <= hc.x1 + 6 and cells[n].x1 >= hc.x0 - 6 and n not in by_col.values():
            by_col[role] = n
    used: set[int] = set()
    g = lambda r: vals[by_col[r]] if r in by_col else None  # noqa: E731
    q, p, t, d = g("qty"), g("price"), g("total"), g("disc")
    ok = False
    if q is not None and p is not None and t is not None and _closes(q, p, t, d):
        li.quantity, li.unit_price, li.total = q, p, t
        used |= {by_col["qty"], by_col["price"], by_col["total"]}
        if d:
            li.discount_pct = d
            used.add(by_col["disc"])
        ok = True
    else:
        idx = range(len(vals))
        closing = []                      # every (qty, price, total[, disc%]) reading that closes
        for i, j, k in permutations(idx, 3):
            if k < max(i, j) or vals[j] == 0 or abs(vals[i]) >= 100000:
                continue
            if _closes(vals[i], vals[j], vals[k], None):
                closing.append((i, j, k, None))
            for m in idx:
                if m not in (i, j, k) and 0 < vals[m] < 100 and _closes(vals[i], vals[j], vals[k], vals[m]):
                    closing.append((i, j, k, m))
                # price quoted per price-unit: qty × price / PU = total, with PU printed on the row
                if m not in (i, j, k) and vals[m] in (10, 100, 1000) and \
                        _closes(vals[i], vals[j] / vals[m], vals[k], None):
                    closing.append((i, j, k, ("pu", m)))
                # a discount printed as % AND value: qty × price × d% = discount value (k)
                if m not in (i, j, k) and 0 < vals[m] < 100 and vals[k] > 0 and "%" in cells[m].text and \
                        near(money(vals[i] * vals[j] * vals[m] / HUNDRED), vals[k], Decimal("0.011")):
                    closing.append((i, j, k, ("dv", m)))
        if not closing:
            # price × d% = discount value, quantity not separately printed (fused into the text)
            for j, m, k in permutations(idx, 3):
                if 0 < vals[m] < 100 and "%" in cells[m].text and vals[k] > 0 and vals[j] > vals[k] and \
                        near(money(vals[j] * vals[m] / HUNDRED), vals[k], Decimal("0.011")):
                    li.unit_price, li.discount_pct, li.discount_amount = vals[j], vals[m], vals[k]
                    li.total = vals[j] - vals[k]
                    used |= {j, m, k}
                    ok = True
                    break
        if closing:
            def plausibility(r):
                i, j, k, m = r
                qtxt = cells[i].text.strip()
                score = 2 if _QTY_UNIT.match(qtxt) or _ORD_DEL.match(qtxt) or _QTY_TIMES.match(qtxt) else 0
                score += 1 if vals[i] == vals[i].to_integral_value() and vals[j] != vals[j].to_integral_value() else 0
                score += 1 if "qty" in roles and abs(cells[i].xc - roles["qty"].xc) < abs(cells[j].xc - roles["qty"].xc) else 0
                score += 1 if "price" in roles and abs(cells[j].xc - roles["price"].xc) < abs(cells[i].xc - roles["price"].xc) else 0
                score += 1 if k == max(n for n in idx if n not in pct_cells) else 0     # the extension is rightmost
                score -= 1 if m is not None else 0
                return score
            i, j, k, m = max(closing, key=plausibility)
            li.quantity, li.unit_price, li.total = vals[i], vals[j], vals[k]
            used |= {i, j, k}
            if isinstance(m, tuple) and m[0] == "dv":
                li.discount_pct, li.discount_amount = vals[m[1]], vals[k]
                li.total = money(vals[i] * vals[j]) - vals[k]       # net after the printed discount
                used.add(m[1])
            elif isinstance(m, tuple):
                used.add(m[1])                 # the price unit; solve.py proves the scaling
            elif m is not None:
                li.discount_pct = vals[m]
                used.add(m)
            ok = True
            # the gross extension closes, and the row also prints its discount as % and value
            # (gross × d% = value): the line's net is gross − value
            if li.discount_pct is None:
                for dm in idx:
                    for dv in idx:
                        if len({dm, dv} | used) == len(used) + 2 and "%" in cells[dm].text and \
                                0 < vals[dm] < 100 and vals[dv] > 0 and \
                                near(money(li.total * vals[dm] / HUNDRED), vals[dv], Decimal("0.011")):
                            li.discount_pct, li.discount_amount = vals[dm], vals[dv]
                            li.total = li.total - vals[dv]
                            used |= {dm, dv}
                            break
                    if li.discount_pct is not None:
                        break
        elif ok:
            pass                               # solved by the discount-value relation above
        elif "total" in roles and t is None and p is not None and \
                sum(bool(re.search(r"\d[.,]\d{2}(?!\d)", c.text)) for c in cells) <= 1:
            # a price under the price column but nothing under the amount column: a component
            # listed inside a set, not a billed line
            return li, False, []
        else:
            if t is not None:
                li.total = t
                used.add(by_col["total"])
            else:
                # the extension is the last money amount on the row; a row without one (only a
                # quantity and a rate) bills nothing
                money_idx = [n for n in idx if n not in pct_cells and vals[n].as_tuple().exponent <= -2]
                if money_idx:
                    k = money_idx[-1]
                    li.total = vals[k]
                    used.add(k)
            if q is not None and by_col["qty"] not in used:
                li.quantity = q
                used.add(by_col["qty"])
            if p is not None and by_col["price"] not in used:
                li.unit_price = p
                used.add(by_col["price"])
    # an explicit "20%" on the row is that row's rate
    for n in sorted(pct_cells):
        if n not in used and 0 <= vals[n] <= 30 and vals[n] != li.discount_pct:
            li.tax_rate = vals[n]
            used.add(n)
            break
    leftovers = [(cells[n], vals[n]) for n in range(len(nums)) if n not in used]
    return li, ok, leftovers


@rule("items.rate_column", "A rate column needs a rate header and holds a few rate-like values (23, 8.10 - not 2.68).")
def _assign_rate_column(rows: list[tuple[Line, list[tuple[Cell, Decimal]]]], roles: dict[str, Cell]) -> None:
    """A tax-rate column is recognised by what it contains: at one x-position on most rows, small
    values (0-30) drawn from a handful of rates. A matching per-line tax amount is then looked up."""
    if "rate" not in roles:
        return                          # without a rate column header, small numbers are not rates
    pts = [(c.xc, v, r) for r, (li, left) in enumerate(rows) for c, v in left if li.tax_rate is None]
    if not pts or len(rows) < 1:
        return
    width = max(c.x1 - c.x0 for _, left in rows for c, _ in left) if any(left for _, left in rows) else 10
    best = None
    for x, _, _ in pts:
        col = [(xx, v, r) for xx, v, r in pts if abs(xx - x) <= max(width, 20)]
        rows_hit = {r for _, _, r in col}
        vals = [v for _, v, _ in col]
        rate_like = all(v.normalize().as_tuple().exponent >= -1 for v in vals)   # 23, 24.0, 8.10 — not 2.68
        if rate_like and all(0 <= v <= 30 for v in vals) and len(set(vals)) <= 4 \
                and len(rows_hit) >= max(1, 0.6 * len(rows)):
            near_hdr = abs(x - roles["rate"].xc) if "rate" in roles else 0
            key = (len(rows_hit), -near_hdr, x)          # codes sit left, rates sit right
            if best is None or key > best[0]:
                best = (key, x)
    if best is None:
        return
    x = best[1]
    for li, left in rows:
        cand = [(c, v) for c, v in left if abs(c.xc - x) <= max(width, 20) and 0 <= v <= 30]
        if cand and li.tax_rate is None:
            li.tax_rate = cand[0][1]
    for li, left in rows:
        if li.tax_rate is not None and li.total is not None and li.tax_rate > 0:
            for c, v in left:
                if v != li.tax_rate and near(money(li.total * li.tax_rate / HUNDRED), v, Decimal("0.02")):
                    li.tax_amount = v


def _closes(q, p, t, d) -> bool:
    if q is None or p is None or t is None:
        return False
    ext = q * p
    if d:
        ext = ext * (1 - d / HUNDRED)
    return near(money(ext), t, Decimal("0.011")) and t != 0 or (t == 0 and q * p == 0 and q is not None)


@rule("items.wrapped_header", "A column header may wrap onto the next line; a 1-2 letter fragment completes the word above.")
def _header_at(lines: list[TextLine], h: int) -> tuple[list[Cell], int] | None:
    """A column header on line h — possibly wrapped onto the next line ('QTY/' over
    'NO. OF USERS', 'EXTENDED' over 'AMOUNT'). Returns (cells, index of last header line)."""
    hc = cells_of(lines[h])
    if any(_is_num(c.text) for c in hc):
        return None
    if h + 1 < len(lines):
        nxt = lines[h + 1]
        nc = cells_of(nxt)
        if nc and not any(_is_num(c.text) for c in nc) and nxt.y0 - lines[h].y1 < 1.2 * lines[h].height:
            merged = [Cell(c.text, c.x0, c.x1) for c in hc]
            for d in nc:
                tgt = max(merged, key=lambda c: min(c.x1, d.x1) - max(c.x0, d.x0))
                if min(tgt.x1, d.x1) - max(tgt.x0, d.x0) > 0:
                    # 'Quantit' over 'y': a 1-2 letter lowercase fragment completes the word above
                    sep = "" if re.fullmatch(r"[a-zà-ÿ]{1,2}", d.text.strip()) else " "
                    tgt.text, tgt.x0, tgt.x1 = tgt.text + sep + d.text, min(tgt.x0, d.x0), max(tgt.x1, d.x1)
                else:
                    merged.append(Cell(d.text, d.x0, d.x1))
            merged.sort(key=lambda c: c.x0)
            if _is_header(merged) and len(_header_roles(merged)) > len(_header_roles(hc)):
                return merged, h + 1
    return (hc, h) if _is_header(hc) else None


@rule("items.table_ends", "An item table ends at a vertical gap, a totals row that does not close, or a tax line.")
@rule("items.inclusive_column", "A price column whose header says it includes tax marks the lines tax-inclusive.")
def extract_items(lines: list[TextLine], dec) -> tuple[list[Line], float, int, int]:
    """Find the line-item table: a header row, then rows until the summary block.
    Returns (lines, consistency, first_row_index, last_row_index)."""
    best: tuple[list[Line], float, int, int] = ([], 0.0, -1, -1)
    for h in range(len(lines)):
        found = _header_at(lines, h)
        if not found:
            continue
        hc, h_end = found
        roles = _header_roles(hc)
        items: list[Line] = []
        leftovers: list[tuple[Line, list]] = []
        closed = 0
        last = h_end
        last_y1 = lines[h_end].y1
        for r in range(h_end + 1, len(lines)):
            cells = cells_of(lines[r])
            txt = lines[r].text
            if lines[r].y0 - last_y1 > 4 * lines[r].height:      # vertical gap: the table has ended
                break
            last_y1 = lines[r].y1
            if _summary_label(txt):
                nums_here, _ = row_numbers(cells, dec)
                v = [x for _, x in nums_here]
                if not any(_closes(v[i], v[j], v[k], None) for i in range(len(v)) for j in range(len(v))
                           for k in range(len(v)) if len({i, j, k}) == 3):
                    break                                  # a totals row ends the item table
            nums, uom = row_numbers(cells, dec)
            used = {id(c) for c, _ in nums}
            pct = {n for n, (c, _) in enumerate(nums) if c.text.strip().endswith("%")}
            desc = " ".join(c.text for c in cells if id(c) not in used)
            desc_cells = [c for c in cells if id(c) not in used and not is_code(c.text)]
            label_txt = " ".join(c.text for c in desc_cells)
            if lx.has(label_txt, lx.TAX_WORDS) and "%" in txt and len(nums) <= 3:
                if items:
                    break                                      # a tax line closes the item table
                continue                                       # ...and is never an item itself
            if not nums:
                if items and desc and len(txt) < 120 and not _is_header(cells):
                    if r - last <= 2:
                        items[-1].description = (items[-1].description + " " + desc).strip()
                        last_y1 = lines[r].y1
                        continue
                if r - last > (3 if items else 10):        # long descriptions may precede the first priced row
                    break
                continue
            if len(nums) == 1 and not desc_cells:
                if r - last > 2:
                    break
                continue
            li, ok, left = _row_from_numbers(desc, nums, roles, pct)
            if li.total is None:
                continue
            li.uom = uom
            items.append(li)
            leftovers.append((li, left))
            closed += ok
            last = r
            last_y1 = lines[r].y1
        if items:
            _assign_rate_column(leftovers, roles)
            priced = [roles[r].text for r in ("price", "total") if r in roles]
            if any(lx.has(t, lx.INCL_HINT) for t in priced) and not any(lx.has(t, lx.EXCL_HINT) for t in priced):
                for li in items:
                    li.price_basis = "gross"           # the column header says prices include tax
            cons = closed / len(items)
            if (cons, len(items)) > (best[1], len(best[0])):
                best = (items, cons, h_end + 1, last)
    if not best[0]:
        best = _closing_rows(lines, dec)
    return best


@rule("items.tax_line_not_item", "A line naming a tax with a % (VAT @16%) is a tax line, never an item.")
def _is_tax_line(text: str) -> bool:
    """'VAT @16%', 'IVA 23 %': a tax line, never an item."""
    return "%" in text and bool(lx.has(text, lx.TAX_WORDS)) and len(text) < 80


@rule("items.headerless_rows", "Without a header, described rows whose own money arithmetic closes are the items.")
def _closing_rows(lines: list[TextLine], dec) -> tuple[list[Line], float, int, int]:
    """No recognisable column header: the item rows are the described rows whose own numbers
    close (qty × price = total), taken as one contiguous block above the totals."""
    rows: list[tuple[int, Line, list]] = []
    for r, ln in enumerate(lines):
        if _summary_label(ln.text) or _is_tax_line(ln.text):
            if rows:
                break
            continue
        cells = cells_of(ln)
        nums, uom = row_numbers(cells, dec)
        used = {id(c) for c, _ in nums}
        desc = " ".join(c.text for c in cells if id(c) not in used)
        if len(nums) < 3 or not re.search(r"[A-Za-z]{3}", desc):
            continue
        pct = {n for n, (c, _) in enumerate(nums) if "%" in c.text}
        li, ok, left = _row_from_numbers(desc, nums, {}, pct)
        if ok and li.total and li.total.as_tuple().exponent <= -2:      # an extension is a money amount
            li.uom = uom
            rows.append((r, li, left))
    if not rows:
        return [], 0.0, -1, -1
    _assign_rate_column([(li, left) for _, li, left in rows], {})
    return [li for _, li, _ in rows], 1.0, rows[0][0], rows[-1][0]


@rule("items.rows_summing_to_base", "Described single-amount rows that add up to a printed subtotal or tax base are the items.")
def _summing_rows(lines: list[TextLine], dec, info: PageInfo):
    """No item table was recognised. A contiguous run of described rows, each carrying one
    amount (and maybe its VAT rate), whose amounts add up to a printed subtotal or tax base IS the
    item list — the page's own arithmetic proves it."""
    targets = {v for v in [info.subtotal, *(t.base for t in info.tax_rows)] if v}
    if not targets:
        return None
    rows: list[tuple[int, Line]] = []
    for r, ln in enumerate(lines):
        cells = cells_of(ln)
        is_rate = lambda c: bool(re.fullmatch(r"\s*\d{1,2}(?:[.,]\d{1,2})?\s?%\s*", c.text))  # noqa: E731
        money_cells = [(c, num(c.text, dec)) for c in cells if re.search(r"\d[.,]\d{2}(?!\d)", c.text)
                       and not is_rate(c) and num(c.text, dec) is not None]
        desc = " ".join(c.text for c in cells if num(c.text, dec) is None and not is_rate(c))
        if len(money_cells) == 1 and re.search(r"[A-Za-z]{3}", desc) and not _summary_label(desc) \
                and not lx.has(desc, lx.TAX_WORDS):
            rate = next((parse_amount(c.text.replace("%", ""), dec) for c in cells if is_rate(c)), None)
            rows.append((r, Line(description=desc.strip(), total=money_cells[0][1],
                                 tax_rate=rate if rate is not None and 0 <= rate <= 30 else None)))
    for i in range(len(rows)):
        total = Decimal(0)
        for j in range(i, len(rows)):
            if j > i and rows[j][0] - rows[j - 1][0] > 2:
                break
            total += rows[j][1].total
            if any(near(total, t, Decimal("0.01")) for t in targets) and j > i:
                return [li for _, li in rows[i:j + 1]], rows[i][0], rows[j][0]
    return None
