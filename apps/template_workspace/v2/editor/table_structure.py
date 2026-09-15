"""Native table hierarchy; never infer groups from repeated scientific text.

Indices are zero-based grid columns and row boundaries (boundary 2 is below
rows 0 and 1). No cell is split, merged, recreated or moved by this module.
"""
from dataclasses import dataclass
import re
from apps.template_workspace.v2.ooxml.namespaces import NS, qn


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(cell):
    return ''.join(cell.xpath('./w:p//w:t/text()|./w:p//m:t/text()', namespaces=NS)).strip()


def _on(node):
    return node is not None and node.get(qn('w:val'), '1') not in {'0', 'false', 'off'}


@dataclass
class Cell:
    node: object
    row: int
    start: int
    end: int
    merge: str | None
    text: str


@dataclass
class Merge:
    start_row: int
    end_row: int  # exclusive
    start_col: int
    end_col: int
    text: str


@dataclass
class TableStructure:
    rows: list[list[Cell]]
    header_rows: int
    group_boundaries: list[int]
    group_column: int | None
    merges: list[Merge]
    valid: bool
    nested: bool

    @property
    def hierarchical(self):
        return self.valid and not self.nested and (self.header_rows > 1 or self.group_column is not None)

    def crosses_merge(self, boundary):
        return any(m.start_row < boundary < m.end_row for m in self.merges)

    def report(self):
        return {'header_rows': self.header_rows, 'group_boundaries': self.group_boundaries,
                'group_column': self.group_column,
                'hierarchical': self.hierarchical, 'valid_topology': self.valid, 'nested': self.nested}


def analyze_table_structure(table):
    """Use repeat-header flags, span subdivision and native vertical merges.

    The leftmost textual body merge defines the outer grouping level. Inner
    subgroup merges are retained, but never cause a rule through an outer cell.
    Unmerged repeated labels/blank cells are deliberately NOT inferred as groups.
    """
    rows, merges, active = [], [], {}
    valid = True
    row_nodes = table.findall('w:tr', NS)
    for ri, row in enumerate(row_nodes):
        before = row.find('w:trPr/w:gridBefore', NS)
        cursor = _int(before.get(qn('w:val'))) if before is not None else 0
        cells, following = [], {}
        for node in row.findall('w:tc', NS):
            span = node.find('w:tcPr/w:gridSpan', NS)
            width = _int(span.get(qn('w:val')), 1) if span is not None else 1
            if width < 1 or node.find('w:tcPr/w:hMerge', NS) is not None:
                valid = False  # old hMerge / malformed grids: conservative fallback
            width = max(1, width)
            vm = node.find('w:tcPr/w:vMerge', NS)
            mode = vm.get(qn('w:val'), 'continue') if vm is not None else None
            cell = Cell(node, ri, cursor, cursor+width, mode, _text(node))
            cells.append(cell)
            key = (cell.start, cell.end)
            if mode == 'restart':
                merge = Merge(ri, ri+1, *key, cell.text)
                merges.append(merge); following[key] = merge
            elif mode == 'continue':
                if key not in active:
                    valid = False
                else:
                    active[key].end_row = ri+1
                    following[key] = active[key]
            elif mode is not None:
                valid = False
            cursor += width
        active = following
        rows.append(cells)
    merges = [m for m in merges if m.end_row > m.start_row+1]
    nested = bool(table.findall('.//w:tbl', NS))
    header = 0
    for row in row_nodes:
        if not _on(row.find('w:trPr/w:tblHeader', NS)):
            break
        header += 1
    if not header and rows:
        # A group label plus actual numeric values can start a headerless table.
        # Keep the one-row fallback for ordinary ambiguous flat tables.
        singles = [c for c in rows[0] if c.merge is None and c.end-c.start == 1 and c.text]
        numeric = sum(bool(re.fullmatch(r'[\s\d.,+−–\-±%()/]+', c.text)) for c in singles)
        headerless = (numeric > len(singles)/2 and
                      (any(c.merge == 'restart' for c in rows[0]) or numeric == len(rows[0])))
        header = 0 if headerless else 1
    # Follow only spans belonging to the current heading. Always leave body rows.
    if valid and header:
        while header < min(len(rows), 8):
            extended = max([header] + [m.end_row for m in merges if m.start_row < header])
            if extended == header:
                for cell in rows[header-1]:
                    if cell.end-cell.start <= 1 or not cell.text or cell.merge == 'continue':
                        continue
                    children = [c for c in rows[header] if cell.start <= c.start and c.end <= cell.end]
                    if (len(children) > 1 and children[0].start == cell.start and children[-1].end == cell.end
                            and all(a.end == b.start for a,b in zip(children,children[1:]))):
                        extended = header+1
                        break
            if extended == header or extended >= len(rows) or extended > 8:
                break
            header = extended
    header = min(header, max(0, len(rows)-1))
    boundaries, group_column = [], None
    candidates = [m for m in merges if m.start_row >= header and re.search(r'[^\W\d_]', m.text)]
    if valid and candidates:
        col = min(m.start_col for m in candidates)
        # Labels must be the first occupied column, not a merged numeric result.
        if all(row and row[0].start == col for row in rows[header:]):
            labels = [row[0] for row in rows[header:]]
            if all(c.merge == 'continue' or c.text for c in labels):
                group_column = col
                for cell in labels[1:]:
                    boundary = cell.row
                    if cell.merge != 'continue' and not any(m.start_row < boundary < m.end_row for m in merges):
                        boundaries.append(boundary)
    return TableStructure(rows, header, boundaries, group_column, merges, valid, nested)
