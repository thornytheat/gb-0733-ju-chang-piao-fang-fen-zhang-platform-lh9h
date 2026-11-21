import csv
import io
import sqlite3
from io import StringIO

import pytest

from app.config import SOURCE_COLUMNS
from app.manifest import checksum
from app.pipeline import compute
from app.sources import sample_sources
from app.store import Store


def write(source, rows):
    stream = StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(SOURCE_COLUMNS[source])
    writer.writerows(rows)
    return stream.getvalue().encode()


def fx_file(rows):
    return write('fx_rates.csv', rows)


def usd_book(rate_paid='7.1000', rate_refund='7.1500', extra_fx=None, drop_refund_fx=False):
    files = sample_sources()
    fx = [
        ('USD', '2026-11-05', rate_paid, '海外行', '锁汇'),
        ('USD', '2026-11-21', rate_refund, '海外行', '重估'),
        ('HKD', '2026-11-05', '0.9200', '海外行', ''),
        ('HKD', '2026-11-21', '0.9100', '海外行', ''),
    ]
    if extra_fx:
        fx.extend(extra_fx)
    if drop_refund_fx:
        fx = [row for row in fx if not (row[0] == 'USD' and row[1] == '2026-11-21')]
    files['fx_rates.csv'] = write('fx_rates.csv', fx)
    return files


def test_paid_rate_is_locked_and_published_allocation_never_revalues(sample):
    """原票人民币分账按收款日汇率锁定，后续汇率变化不改写。"""
    ticket = next(row for row in sample['tickets'] if row['ticket'] == '00201')
    assert ticket['currency'] == 'USD' and ticket['locked_rate'] == '7.1000'
    # 100 USD * 7.10 = 710.00 元
    assert ticket['paid_minor'] == 10_000 and ticket['paid_cents'] == 71_000
    allocations = [row for row in sample['publication']['allocations'] if row['ticket'] == '00201']
    assert sum(row['allocated_cents'] for row in allocations) == 71_000
    assert all(row['locked_rate'] == '7.1000' for row in allocations)


def test_refund_reverses_locked_cny_but_books_fx_difference_separately(sample):
    """退款冲回锁定人民币分账；重估额与锁定额之差进独立汇差槽。"""
    refund = next(row for row in sample['refunds'] if row['refund_id'] == 'R201-01')
    # 50 USD：锁定冲回 50*7.10=355.00；退款日重估 50*7.15=357.50；汇差 +2.50 元
    assert (refund['reversal_cents'], refund['revalued_cents'], refund['fx_difference_cents']) == (
        -35_500, -35_750, 250)
    hkd = next(row for row in sample['refunds'] if row['refund_id'] == 'R202-01')
    # 100 HKD：锁定 92.00，重估 91.00，汇差 -1.00 元
    assert (hkd['reversal_cents'], hkd['revalued_cents'], hkd['fx_difference_cents']) == (-9_200, -9_100, -100)
    venue = next(row for row in sample['funding']['venues'] if row['venue'] == '星港演艺中心')
    # 场馆净应收只受锁定额影响；汇差独立归属，不进应收
    assert venue['payable_cents'] == 35_500 and venue['fx_difference_cents'] == 150
    assert venue['revalued_net_cents'] == venue['net_cents'] - venue['fx_difference_cents']


def test_same_ticket_keeps_two_nets_and_fx_is_fully_attributed(sample):
    """同一张票两个净额：锁定净额与重估净额，差额即汇差，且全部有去向。"""
    ticket = next(row for row in sample['funding']['tickets'] if row['ticket'] == '00201')
    assert ticket['fx_difference_cents'] == 250
    assert ticket['revalued_net_cents'] == ticket['net_cents'] - ticket['fx_difference_cents']
    totals = sample['funding']['totals']
    venue_fx = sum(row['fx_difference_cents'] for row in sample['funding']['venues'])
    assert venue_fx == totals['fx_difference_cents'] == 150
    assert sample['funding']['checks']['fx_attributed'] is True
    assert sample['funding']['balanced'] is True


def test_missing_refund_date_fx_fact_blocks_publication():
    """汇率事实不全（缺退款日 USD 汇率）时整批失败，不发布。"""
    files = usd_book(drop_refund_fx=True)
    with pytest.raises(Exception) as exc:
        compute('fx-incomplete', 'unit', files)
    assert exc.value.code == 'FX_FACT_MISSING'


def test_missing_paid_date_fx_fact_blocks_publication():
    files = usd_book()
    files['fx_rates.csv'] = fx_file([row for row in csv.reader(io.StringIO(files['fx_rates.csv'].decode()))
                                     if row and not (row[0] == 'USD' and row[1] == '2026-11-05')][1:])
    with pytest.raises(Exception) as exc:
        compute('fx-no-paid-rate', 'unit', files)
    assert exc.value.code == 'FX_FACT_MISSING'


def test_conflicting_fx_rates_are_quarantined_and_absent_from_book():
    # 矛盾汇率放在不参与本批折汇的日期，避免必需汇率缺失，仅验证隔离
    files = usd_book(extra_fx=[('USD', '2026-12-01', '7.2000', '对手行', '矛盾汇率'),
                               ('USD', '2026-12-01', '7.3000', '对手行', '矛盾汇率')])
    snapshot = compute('fx-conflict', 'unit', files)
    conflicts = [q for q in snapshot['quarantine'] if q['code'] == 'FX_RATE_CONFLICT']
    assert conflicts and conflicts[0]['key'] == 'USD/2026-12-01'
    assert not any(row['currency'] == 'USD' and row['rate_date'] == '2026-12-01' for row in snapshot['fx'])


def test_refund_currency_mismatch_is_rejected(files, source_writer):
    raw = source_writer('refunds.csv', [('RX', '00201', 'G-NIGHT', '2026-11-21T15:00:00+08:00',
                                         '10.00', '元', 'HKD')])
    snapshot = compute('fx-mismatch', 'unit', {**files, 'refunds.csv': raw})
    issue = next(q for q in snapshot['quarantine'] if q['key'] == 'RX')
    assert issue['code'] == 'REFUND_CURRENCY_MISMATCH'


def test_foreign_cap_enforced_in_foreign_minor_unit(files, source_writer):
    # G-NIGHT 原外币分账 50 USD，请求退 60 USD 必须被外币硬限额挡住
    raw = source_writer('refunds.csv', [('RX', '00201', 'G-NIGHT', '2026-11-21T15:00:00+08:00',
                                         '60.00', '元', 'USD')])
    snapshot = compute('fx-cap', 'unit', {**files, 'refunds.csv': raw})
    issue = next(q for q in snapshot['quarantine'] if q['key'] == 'RX')
    assert issue['code'] == 'REFUND_FOREIGN_CAP_EXCEEDED'
    assert issue['detail']['available_minor'] == 5_000


def test_rerun_same_batch_is_idempotent_and_keeps_one_settlement(tmp_path, files):
    """同一批次重跑不新增结算记录、不重复冲款。"""
    store = Store(tmp_path / 'fx.sqlite3')
    store.initialize()
    first, reused_first = store.commit('fx-ledger', files)
    second, reused_second = store.commit('fx-ledger', files)
    assert reused_first is False and reused_second is True
    assert first['manifest']['run_id'] == second['manifest']['run_id']
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM runs').fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM run_inputs').fetchone() == (6,)
    assert [r['refund_id'] for r in second['refunds']] == [r['refund_id'] for r in first['refunds']]


def test_failed_publish_restores_prior_results_and_keeps_approved_refunds(tmp_path, files):
    """发布失败后旧版结果仍可读取，原分账与已确认退款记录不丢失。"""
    store = Store(tmp_path / 'atomic.sqlite3')
    store.initialize()
    snapshot, reused = store.commit('fx-atomic', files)
    prior_run = snapshot['manifest']['run_id']
    prior_refunds = [row['refund_id'] for row in snapshot['refunds']]
    with sqlite3.connect(store.path) as db:
        db.executescript("""
            CREATE TRIGGER fail_fx BEFORE INSERT ON run_inputs
            WHEN NEW.source = 'fx_rates.csv'
            BEGIN SELECT RAISE(ABORT, 'storage stopped'); END;
        """)
    # 用不同账册名确保走到插入分支（同名重跑会幂等复用、不触发存储写入）
    with pytest.raises(sqlite3.IntegrityError, match='storage stopped'):
        store.commit('fx-atomic-retry', files)
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM runs').fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM publications').fetchone() == (1,)
    recovered = store.get(prior_run)
    assert recovered['manifest']['run_id'] == prior_run
    assert [row['refund_id'] for row in recovered['refunds']] == prior_refunds
    assert recovered['funding']['balanced'] is True


def test_locked_refunds_survive_stale_resubmission(upload, files):
    """补报遗漏已入账的外币冲正时返回 REFUND_CHANGED，不丢失原冲正。"""
    first = upload(files)
    assert first.status_code == 201
    stale = {**files, 'refunds.csv': files['refunds.csv'].splitlines(keepends=True)[0]}
    response = upload(stale)
    assert (response.status_code, response.json()['code']) == (409, 'REFUND_CHANGED')


def test_statement_download_reports_venue_net_and_fx(client):
    run_id = client.post('/api/runs/sample').json()['run_id']
    response = client.get(f'/api/reconcile/{run_id}/statement')
    assert response.status_code == 200 and response.content.startswith(b'\xef\xbb\xbfvenue')
    rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
    venue = next(row for row in rows if row['venue'] == '星港演艺中心')
    assert (venue['payable_cents'], venue['fx_difference_cents']) == ('35500', '150')


def test_fx_policy_recorded_in_manifest_and_bundle(client):
    run_id = client.post('/api/runs/sample').json()['run_id']
    manifest = client.get('/api/evidence/' + run_id).json()
    assert manifest['manifest']['parameters']['fx_policy']['lock'] == 'ticket_paid_at_rate'
    archive = client.get(f'/api/evidence/{run_id}/export')
    from zipfile import ZipFile
    with ZipFile(io.BytesIO(archive.content)) as zipped:
        assert 'evidence/fx-rates.csv' in zipped.namelist()
        assert 'evidence/venue-statement.csv' in zipped.namelist()
        names = zipped.namelist()
        assert f'source/fx_rates.csv' in names
