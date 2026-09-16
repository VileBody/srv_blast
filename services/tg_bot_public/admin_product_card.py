"""Первый экран админки — «Обзор продукта» в направлении «Студия».

Чистый рендер: на вход результат `product_metrics.compute()` + история расходов,
на выход HTML. Никаких запросов — всё, что здесь показано, воспроизводится
на синтетических данных (`scripts/dev_admin_preview.py`).

Композиция (согласована по макету): пять именованных панелей —
Пользователи / Продукт / Деньги / Воронка / Возвращаемость — в 12-колоночной
сетке; переключатели источника и периода в шапке блока, окно активности —
внутри «Пользователей».
"""
from __future__ import annotations

import html as html_mod
from typing import Any, Dict, List, Optional

CHANNEL_LABELS = {"site": "Сайт", "bot": "Бот", "all": "Вместе"}
PERIOD_LABELS = (("7d", "7 дн"), ("30d", "30 дн"), ("90d", "90 дн"))

CARD_CSS = """
  .pm-head { display: flex; align-items: center; justify-content: space-between; gap: 20px; flex-wrap: wrap; margin: 1.6rem 0 1.3rem; }
  .pm-head h1 { margin: 0; }
  .pm-head .sub { color: var(--text-50); font-size: .92em; }
  .pm-head .ctl { display: flex; gap: 10px; flex-wrap: wrap; }
  .pm-grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 18px; }
  .pm-panel { background: var(--surface); border-radius: var(--r-card); box-shadow: var(--shadow); padding: 22px 24px; display: flex; flex-direction: column; gap: 18px; min-width: 0; }
  .pm-panel .ph { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
  .pm-panel .ph b { font-weight: 600; font-size: 1.07em; letter-spacing: -0.01em; }
  .pm-panel .ph span { color: var(--text-50); font-size: .86em; font-weight: 400; }
  .pm-panel .ph a { font-size: .86em; }
  .pm-kpis { display: grid; gap: 18px 22px; }
  .pm-kpi { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .pm-kpi .l { font-size: .86em; color: var(--text-50); }
  .pm-kpi .v { font-size: 2.05em; font-weight: 700; letter-spacing: -0.03em; line-height: 1; white-space: nowrap; }
  .pm-kpi .v.acc { color: var(--accent); }
  .pm-kpi .s { font-size: .84em; color: var(--text-35); }
  .pm-down { color: var(--danger); } .pm-up { color: var(--ok); }
  .pm-seg { display: inline-flex; background: var(--surface-2); border-radius: 999px; padding: 2px; gap: 2px; }
  .pm-seg a { padding: 4px 10px 2px; border-radius: 999px; color: var(--text-50); font-size: .8em; text-decoration: none; line-height: 1.3; }
  .pm-seg a.on { background: var(--text); color: var(--bg); font-weight: 600; }
  .pm-seg a:hover { text-decoration: none; color: var(--text); }
  .pm-funnel { display: flex; flex-direction: column; gap: 9px; }
  .pm-funnel-row { display: grid; grid-template-columns: 150px 1fr 84px; align-items: center; gap: 12px; font-size: .93em; }
  .pm-funnel-row .bar { height: 22px; background: var(--surface-2); border-radius: 999px; overflow: hidden; }
  .pm-funnel-row .bar i { display: block; height: 100%; background: var(--accent); border-radius: 999px; min-width: 6px; }
  .pm-funnel-row .bar i.last { background: var(--ok); }
  .pm-funnel-row .n { text-align: right; font-weight: 600; white-space: nowrap; }
  .pm-funnel-row .n span { color: var(--text-35); font-weight: 400; margin-left: 4px; }
  .pm-ret { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
  .pm-ret div.t { background: var(--surface-2); border-radius: 12px; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
  .pm-ret .l { font-size: .84em; color: var(--text-50); }
  .pm-ret .v { font-size: 1.7em; font-weight: 700; letter-spacing: -0.02em; line-height: 1; }
  .pm-ret .s { font-size: .82em; color: var(--text-35); }
  .pm-cohort { display: grid; grid-template-columns: 90px 50px repeat(5, minmax(0, 1fr)); gap: 5px; font-size: .86em; }
  .pm-cohort .h { color: var(--text-35); padding: 4px 0; }
  .pm-cohort .h.c { padding: 4px; text-align: center; }
  .pm-cohort .w { padding: 6px 0; color: var(--text-70); }
  .pm-cohort .c { padding: 6px; text-align: center; border-radius: 7px; }
  .pm-cohort .c.na { color: #4a4470; }
  .pm-note { color: var(--text-35); font-size: .82em; line-height: 1.5; }
  .pm-spend summary { cursor: pointer; color: var(--text-70); font-size: .9em; list-style: none; }
  .pm-spend summary::-webkit-details-marker { display: none; }
  .pm-spend summary::before { content: "+ "; color: var(--text-35); }
  .pm-spend[open] summary::before { content: "− "; }
  .pm-spend form { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 10px; }
  .pm-spend label { font-size: .88em; color: var(--text-70); display: inline-flex; align-items: center; gap: 6px; }
  .pm-spend table { margin-top: 8px; font-size: .86em; }
  @media (max-width: 1100px) { .pm-grid > * { grid-column: span 12 !important; } .pm-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; } }
"""


def _n(value: Optional[float], digits: int = 0) -> str:
    if value is None:
        return "—"
    if digits == 0:
        return f"{int(round(value)):,}".replace(",", " ")
    return f"{value:,.{digits}f}".replace(",", " ")


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.0f}%"


def _pct1(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _rub(value: Optional[float]) -> str:
    return "—" if value is None else f"{_n(value)} ₽"


def _hours(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if value < 1:
        return f"{value * 60:.0f} мин"
    if value < 48:
        return f"{value:.1f} ч"
    return f"{value / 24:.1f} дн"


def _days(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.1f} дн"


def _delta(current: float, previous: float) -> str:
    """«↑ 12% к прошлому (163)» — сравнение с предыдущим периодом той же длины."""
    if not previous:
        return "прошлый период — 0"
    change = (current - previous) / previous * 100.0
    cls = "pm-up" if change >= 0 else "pm-down"
    arrow = "↑" if change >= 0 else "↓"
    return f'<span class="{cls}">{arrow} {abs(change):.0f}%</span> к прошлому ({_n(previous)})'


def _kpi(label: str, value: str, sub: str = "", *, accent: bool = False) -> str:
    return (
        f'<div class="pm-kpi"><div class="l">{label}</div>'
        f'<div class="v{" acc" if accent else ""}">{value}</div>'
        f'<div class="s">{sub or "&nbsp;"}</div></div>'
    )


def _seg(items: List[tuple], current: str, href: str, cls: str = "pm-seg") -> str:
    return f'<div class="{cls}">' + "".join(
        f'<a href="{href.format(key=key)}" class="{"on" if key == current else ""}">{label}</a>' for key, label in items
    ) + "</div>"


def _cohort_cell(v: Optional[float]) -> str:
    if v is None:
        return '<div class="c na">·</div>'
    if v >= 99.5:
        return '<div class="c" style="background:var(--accent);color:#07030f;font-weight:600">100%</div>'
    alpha = 0.08 + 0.55 * (v / 100.0)
    return f'<div class="c" style="background:rgba(155,132,238,{alpha:.2f})">{v:.0f}%</div>'


def render_product_card(
    metrics: Dict[str, Any],
    *,
    channel: str,
    active_period: str,
    period_label: str,
    spend_rows: List[Dict[str, Any]],
    spend_flash: str = "",
) -> str:
    u, p, m = metrics["users"], metrics["product"], metrics["money"]
    ret = metrics["retention"]
    active_days = int(u.get("active_days") or 30)
    sources_period = active_period if active_period in ("7d", "30d", "90d") else "30d"

    channel_seg = _seg(
        list(CHANNEL_LABELS.items()), channel,
        f"/admin/?channel={{key}}&period={active_period}&active={active_days}", cls="seg",
    )
    period_items = list(PERIOD_LABELS) + [("custom", "Даты" if active_period == "custom" else "Даты…")]
    period_seg = '<div class="seg">' + "".join(
        f'<a href="{"#periods" if key == "custom" else f"/admin/?channel={channel}&period={key}&active={active_days}"}" '
        f'class="{"on" if active_period == key else ""}">{label}</a>'
        for key, label in period_items
    ) + "</div>"
    active_seg = _seg(
        [(str(d), str(d)) for d in (7, 30, 90)], str(active_days),
        f"/admin/?channel={channel}&period={active_period}&active={{key}}",
    )

    users_panel = f"""
      <div class="pm-panel" style="grid-column: span 12">
        <div class="ph"><b>Пользователи</b>{active_seg}</div>
        <div class="pm-kpis" style="grid-template-columns: repeat(4, minmax(0, 1fr))">
          {_kpi("Пользователи", _n(u["total"]), f'зарегистрировались {_n(u["registered_total"])} · без заблокировавших бота')}
          {_kpi(f"Активные за {active_days} дн", _n(u["active_30d"]), f'{_pct(u["active_pct"])} · не заблокировали бота', accent=True)}
          {_kpi("Отписались", _n(u["blocked"]), f'{_pct(u["blocked_pct"])} · последнее действие — блок')}
          {_kpi("Новые за период", _n(u["new_period"]), _delta(u["new_period"], u["new_prev"]))}
        </div>
      </div>"""

    product_panel = f"""
      <div class="pm-panel" style="grid-column: span 12">
        <div class="ph"><b>Продукт <span>за период</span></b><span>успех рендера {_pct(p["success_pct"])}</span></div>
        <div class="pm-kpis" style="grid-template-columns: repeat(5, minmax(0, 1fr))">
          {_kpi("Готовые ролики", _n(p["generation_done"]), f'из {_n(p["generation_started"])} запусков', accent=True)}
          {_kpi("Получили ролик", _n(p["creators"]), f'{_pct(p["creators_pct_of_active"])} активных ({_n(u["active_period"])})')}
          {_kpi("Роликов на человека", _n(p["videos_per_creator"], 1), "среди получивших")}
          {_kpi("Вернулись за вторым", _pct(p["repeat_pct"]), f'{_n(p["repeat_creators"])} чел. с ≥2 роликами')}
          {_kpi("До первого ролика", _hours(p["ttv_median_hours"]), "медиана от первого визита")}
        </div>
      </div>"""

    ltv_cac = m.get("ltv_to_cac")
    cac_sub = (
        f'{_rub(m["spend_rub"])} / {_n(m["new_payers_period"])} новых платящих'
        if m["spend_rub"] else "нет расходов за период — внесите ниже"
    )
    if ltv_cac is not None:
        cac_sub = f'<span class="{"pm-up" if ltv_cac >= 3 else "pm-down"}">LTV/CAC {ltv_cac:.1f}</span> · ' + cac_sub
    money_panel = f"""
      <div class="pm-panel" style="grid-column: span 12">
        <div class="ph"><b>Деньги <span>за период · LTV и конверсия — за всё время</span></b><a href="/admin/sources?period={sources_period}">Расходы по источникам →</a></div>
        <div class="pm-kpis" style="grid-template-columns: repeat(6, minmax(0, 1fr))">
          {_kpi("Выручка", _rub(m["revenue_period"]), _delta(m["revenue_period"], m["revenue_prev"]), accent=True)}
          {_kpi("Платящих", _n(m["payers_period"]), f'из них новых {_n(m["new_payers_period"])}')}
          {_kpi("Конверсия в оплату", _pct(m["paid_conversion_pct"]), f'{_n(m["payers_live"])} платящих из {_n(u["total"])}')}
          {_kpi("Средний чек", _rub(m["arppu_period"]), "на платящего за период")}
          {_kpi("LTV", _rub(m["ltv"]), "выручка на платящего")}
          {_kpi("CAC", _rub(m["cac"]), cac_sub)}
        </div>
      </div>"""

    steps = metrics["funnel"]
    rows = "".join(
        f'<div class="pm-funnel-row"><div>{html_mod.escape(step["label"])}</div>'
        f'<div class="bar"><i class="{"last" if i == len(steps) - 1 else ""}" style="width:{max(1.0, step["pct_of_first"] or 0.0):.0f}%"></i></div>'
        f'<div class="n">{_n(step["count"])}<span>{_pct(step["pct_of_first"])}</span></div></div>'
        for i, step in enumerate(steps)
    )
    funnel_panel = f"""
      <div class="pm-panel" style="grid-column: span 5; gap: 14px">
        <div class="ph"><b>Воронка <span>уникальные люди, % от вошедших</span></b></div>
        <div class="pm-funnel">{rows or '<p>Нет данных</p>'}</div>
      </div>"""

    day_head = "".join(f'<div class="h c">D{d}</div>' for d in ret["days"])
    seg_rows = ""
    for seg in ret["segments"]:
        seg_rows += f'<div class="w"><b>{seg["label"]}</b></div><div class="w">{_n(seg["size"])}</div>'
        for c in seg["cells"]:
            seg_rows += f'<div class="c" title="{_n(c["returned"])} из {_n(c["matured"])}">{_pct1(c["pct"])}</div>'
    ret_table = f'<div class="pm-cohort" style="grid-template-columns: 110px 60px repeat({len(ret["days"])}, minmax(0, 1fr))"><div class="h">Сегмент</div><div class="h">чел.</div>{day_head}{seg_rows}</div>'
    cohort = '<div class="h">Когорта</div><div class="h">чел.</div>' + "".join(
        f'<div class="h c">нед {k}</div>' for k in range(ret["cohort_offsets"])
    )
    for c in ret["cohorts"]:
        cohort += f'<div class="w">с {c["week"]}</div><div class="w">{c["size"]}</div>' + "".join(_cohort_cell(v) for v in c["cells"])
    retention_panel = f"""
      <div class="pm-panel" style="grid-column: span 7; gap: 14px">
        <div class="ph"><b>Возвращаемость</b><span>медиана до второго визита {_days(ret["median_days_to_return"])}</span></div>
        {ret_table}
        <div class="pm-note">D<i>n</i> — доля пользователей, сделавших что-то ровно на n-й день после первого визита (только действия человека, рассылки не в счёт). «Платные» — есть хотя бы одна оплата.</div>
        <div class="pm-cohort">{cohort}</div>
        <div class="pm-note">Недельные когорты: строка — неделя первого визита, ячейка — доля когорты с действием в n-ю неделю. Точка — неделя ещё не наступила.</div>
      </div>"""

    spend_table = "".join(
        f'<tr><td>{html_mod.escape(r["month"])}</td><td>{html_mod.escape(r.get("source") or "— общие")}</td><td>{_rub(r["spend_rub"])}</td>'
        f'<td>{html_mod.escape(r["note"])}</td><td class="meta">{html_mod.escape(r["updated_by"])} · {html_mod.escape(r["updated_at"])}</td></tr>'
        for r in spend_rows
    )
    spend_panel = f"""
      <div class="pm-panel" style="grid-column: span 12; padding: 16px 24px">
        <details class="pm-spend">
          <summary>Расходы на маркетинг для CAC · {len(spend_rows)} записей · <a href="/admin/sources?period={sources_period}">по источникам →</a></summary>
          {spend_flash}
          <form method="post" action="/admin/marketing-spend">
            <input type="hidden" name="channel" value="{channel}"><input type="hidden" name="period" value="{active_period}">
            <label>Месяц <input type="month" name="month" required></label>
            <label>Источник <input type="text" name="source" placeholder="пусто = общие" style="width:150px"></label>
            <label>Сумма, ₽ <input type="number" name="spend_rub" min="0" step="1" required style="width:120px"></label>
            <label>Комментарий <input type="text" name="note" maxlength="200" placeholder="реклама TG + блогеры"></label>
            <button type="submit">Сохранить</button>
          </form>
          {f'<div class="table-wrap"><table><tr><th>Месяц</th><th>Источник</th><th>Сумма</th><th>Комментарий</th><th>Кто · когда</th></tr>{spend_table}</table></div>' if spend_table else ''}
        </details>
      </div>"""

    return f"""
    <div class="pm-head">
      <div><h1>Обзор продукта</h1></div>
      <div class="ctl">{channel_seg}{period_seg}</div>
    </div>
    <div class="pm-grid">
      {users_panel}
      {product_panel}
      {money_panel}
      {funnel_panel}
      {retention_panel}
      {spend_panel}
    </div>
    """
