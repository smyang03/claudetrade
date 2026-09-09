"""Read-only selection experiment display. No collector or broker dependency."""
from datetime import datetime, timezone

from runtime.selection_shadow_book import read_report


def selection_shadow_report(path):
    return read_report(path, datetime.now(timezone.utc).isoformat())


PANEL_JS = r'''
function ssEscape(value) {
  return String(value ?? '미확인').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function ssValue(value) {
  if (value === null || value === undefined) return '미확인';
  if (typeof value === 'object') return JSON.stringify(value);
  if (typeof value === 'number') return Number.isFinite(value) ? value.toLocaleString(undefined,{maximumFractionDigits:4}) : '미확인';
  return String(value);
}
function ssTable(title, rows, columns) {
  return '<h3>'+ssEscape(title)+'</h3>' + (rows.length ? '<div style="overflow-x:auto"><table><thead><tr>'+columns.map(c=>'<th>'+ssEscape(c[0])+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+columns.map(c=>'<td>'+ssEscape(ssValue(typeof c[1] === 'function' ? c[1](row) : row[c[1]]))+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>' : '<p>기록 없음 · 첫 유효 관측 대기</p>');
}
function renderSelectionShadow(d) {
  const warning = document.getElementById('selection-shadow-warning');
  if (!d.available) {
    warning.textContent = '조회 불가 · 첫 유효 관측 대기 · '+(d.errors||[]).map(e=>(e.code||e.status)+' '+(e.at||'')).join(' · ');
    return; // Keep the last successful report visible, with its original timestamps.
  }
  warning.textContent = 'SHADOW ONLY · LAST_PRICE_PAPER · US CACHE_ONLY: 기존 관측만 사용, 새 시세가 없으면 미체결. KR 최대 4건/주기: 보유가 많으면 stale 가능. 미확인 시각은 추정하지 않습니다.';
  const experiments = d.experiments || [];
  const markets = d.markets || [];
  const latest = ['KR','US'].map(m=>markets.find(x=>x.market===m)||{market:m,snapshot_status:'MISSING',execution_status:'미실행'});
  let html = '<p>'+ssEscape(d.contract)+' · 최근 원장 '+ssEscape(d.last_updated)+'</p>';
  html += ssTable('실험',experiments,[['시장','market'],['버전','version'],['최초 관측','first_observed_at'],['코드 지문','code_fingerprint'],['파라미터 지문','parameter_fingerprint'],['고정 파라미터','parameters']]);
  html += ssTable('수집 · 시세 · 실행',latest,[['시장','market'],['세션','session_date'],['입력','snapshot_status'],['실행','execution_status'],['최근 상태','latest_status'],['상태 시각','latest_status_at'],['상태 사유','latest_status_details'],['수집 시작','collected_at'],['수집 완료','snapshot_completed_at'],['최근 평가','last_mark_at'],['Heartbeat','heartbeat_at'],['시세 모드/품질','quote_source'],['시세 진단 시각','quote_source_at'],['후보','candidate_count'],['제외','excluded_count'],['원본 행 수','source_row_count'],['제외 사유','excluded_reasons'],['출처 요약','provenance']]);
  html += ssTable('독립 계좌 · KRW',d.accounts||[],[['시장','market'],['규칙','rule'],['초기 자본','capital'],['현금','cash'],['보유 평가액','marked_value'],['청산 비용 예약','exit_fee_reserve'],['비용 후 NAV','nav'],['수익률 %',r=>r.return_pct==null?'첫 유효 관측 대기':r.return_pct],['최대 낙폭 %',r=>r.return_pct==null?'미관측':r.mdd_pct],['노출 %','exposure_pct'],['청산','closed_count'],['보유','open_count'],['성숙 코호트','mature_count'],['Stale','stale_count'],['청산 손익','closed_pnl'],['미청산 손익','open_pnl']]);
  html += ssTable('최근 선택 · 미체결/기권 포함 (최대 100건)',(d.intents||[]).slice(0,100),[['시장','market'],['규칙','rule'],['세션','session_date'],['종목','ticker'],['근거',r=>r.rule==='baseline_k1'?'전일 거래대금 내림차순':'고정 seed'],['Seed','seed'],['배정 KRW','allocation'],['판단 시각','decided_at'],['상태','status'],['미체결/기권 사유','reason'],['체결 시각','fill_at'],['체결가','fill_price'],['수량','fill_qty'],['공급자','fill_source'],['요청','fill_requested_at'],['수신','fill_received_at'],['가격 종류','fill_price_kind']]);
  const common = [['시장','market'],['규칙','rule'],['종목','ticker'],['진입 세션','entry_session'],['진입 시각','entry_at'],['진입가','entry_price'],['수량','qty'],['원가 KRW','entry_cost'],['왕복 비용 KRW','total_fees'],['비용 후 수익률 %','return_pct'],['보유 세션','holding_sessions'],['보유 계산 시각','holding_asof'],['만기 세션','scheduled_exit_session'],['예정 청산 시각','scheduled_exit_at'],['초과 세션','delay_sessions']];
  html += ssTable('보유',d.positions||[],common.concat([['평가가','last_price'],['평가 시각','last_price_at'],['평가 공급자','last_source'],['진입 공급자','entry_source'],['Stale',r=>r.stale?'STALE':'검증 시세'],['청산 대기','pending_reason']]));
  html += ssTable('최근 청산 (최대 100건)',(d.closed||[]).slice(0,100),common.concat([['청산 시각','exit_at'],['청산가','exit_price'],['손익 KRW','pnl'],['사유','reason'],['공급자','exit_source'],['지연',r=>r.delayed?'지연 청산':'아니오']]));
  html += ssTable('최근 상태/결손 (최대 30건)',(d.errors||[]).slice(0,30),[['시장','market'],['세션','session_date'],['상태',r=>r.status||r.code],['시각','at'],['사유','details']]);
  document.getElementById('selection-shadow-content').innerHTML = html;
}
async function loadSelectionShadow() {
  try {
    const response = await fetch('/api/selection_shadow', {cache:'no-store'});
    if (!response.ok) throw Error('HTTP '+response.status);
    renderSelectionShadow(await response.json());
  } catch (error) {
    document.getElementById('selection-shadow-warning').textContent = '조회 실패 · '+new Date().toISOString()+' · 이전 자료 유지';
  }
}
'''

PANEL_HTML = '''<section id="selection-shadow-panel" class="card">
<h2>선택 비교 — 신규 SHADOW</h2>
<p id="selection-shadow-warning" role="status">첫 유효 관측 대기</p>
<div id="selection-shadow-content"></div></section>
<script>''' + PANEL_JS + '''\nloadSelectionShadow();</script>'''
