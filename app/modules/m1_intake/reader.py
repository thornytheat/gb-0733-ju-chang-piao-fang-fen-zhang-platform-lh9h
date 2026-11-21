import csv
import io

from app.config import MAX_SOURCE_BYTES, MAX_SOURCE_ROWS, OPTIONAL_COLUMNS, SOURCE_COLUMNS
from app.errors import Problem
from app.records import SourceRow


def decode(data, name):
    if len(data) > MAX_SOURCE_BYTES:
        raise Problem(413, 'FILE_LIMIT', {'source': name})
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise Problem(422, 'FILE_ENCODING', {'source': name})


def read_source(data, name, stats, isolates):
    text, encoding = decode(data, name)
    if not text:
        raise Problem(422, 'FILE_EMPTY', {'source': name})
    stats.update(encoding=encoding, status='HEADER_ONLY', rows=0, blank=0, headers=0)
    columns = SOURCE_COLUMNS[name]
    optional_column = OPTIONAL_COLUMNS.get(name)
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    header = None
    try:
        while True:
            line = reader.line_num + 1
            fields = next(reader, None)
            if fields is None:
                break
            stats['rows'] += 1
            if stats['rows'] > MAX_SOURCE_ROWS:
                raise Problem(413, 'FILE_LIMIT', {'source': name, 'line': line})
            if not any(field.strip() for field in fields):
                stats['blank'] += 1
                continue
            clean = tuple(field.strip().lstrip('﻿') for field in fields)
            if header is None:
                header = clean
                missing = set(columns) - set(header)
                optional_ok = optional_column and missing == {optional_column}
                if not set(header) <= set(columns) or (len(header) == len(columns) - 1 and not optional_ok) \
                        or len(header) not in (len(columns), len(columns) - 1):
                    raise Problem(422, 'FILE_COLUMNS', {'source': name, 'line': line, 'expected': list(columns)})
                stats['headers'] += 1
                continue
            if clean == header:
                stats['headers'] += 1
                continue
            stats['status'] = 'DATA'
            values = dict(zip(header, fields))
            short_by_optional = (optional_column and len(fields) == len(columns) - 1)
            if short_by_optional:
                # 旧版短行（恰好缺本币列）：补默认本币，按短行自身列宽视为完整。
                values[optional_column] = ''
            row = SourceRow(name, line, reader.line_num, tuple(fields), values)
            if len(fields) != (len(columns) - 1 if short_by_optional else len(header)):
                isolates.add(row, 'COLUMN_COUNT', expected=len(header), actual=len(fields))
                continue
            yield row
    except csv.Error as exc:
        raise Problem(422, 'FILE_CSV', {'source': name, 'line': line}) from exc
    if header is None:
        raise Problem(422, 'FILE_BLANK', {'source': name})
