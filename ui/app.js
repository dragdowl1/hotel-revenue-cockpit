// lisbon lodging cockpit, light theme, pages rendered from the json api

const C = { blue: "#2f6fdb", blueLight: "#b9d0f5", red: "#d64545", redLight: "#f3c2c2", green: "#2e8b57", gray: "#b3bcc5", grayLight: "#dde3e8", text: "#1c2430", muted: "#5f6b7a", faint: "#98a2ad", rule: "#e3e7eb" };
const hotelColor = h => h === "City Hotel" ? C.blue : cats[2];
const reds = ["#7a1010", "#c8352b", "#e8763f", "#f0a48c", "#a8582d"];
const cats = ["#2f6fdb", "#e07b39", "#3b9d8c", "#8e5ea2", "#b58a2f", "#6b7280"];
const hotels = ["City Hotel", "Resort Hotel"];
const capacityOf = h => h === "City Hotel" ? 270 : 260;
const charts = [];
const fmtInt = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });
const fmtEur = new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const fmtPct = (v, d = 0) => v == null ? "" : (v * 100).toFixed(d) + "%";
const fmtK = v => v == null ? "" : Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(2) + "M" : Math.abs(v) >= 1e3 ? (v / 1e3).toFixed(0) + "k" : fmtInt.format(v);
const signed = (v, d = 0) => { const t = (v * 100).toFixed(d); return (parseFloat(t) > 0 ? "+" : "") + t + "%"; };
const sources = {
  wikimedia: `<a href="https://wikimedia.org/api/rest_v1/" target="_blank" rel="noopener">Wikimedia pageviews API</a>`,
  openmeteo: `<a href="https://open-meteo.com/" target="_blank" rel="noopener">Open-Meteo</a>`,
  holidays: `<a href="https://date.nager.at/" target="_blank" rel="noopener">Nager.Date</a> and <a href="https://openholidaysapi.org/" target="_blank" rel="noopener">OpenHolidays API</a>`,
  eurostat: `<a href="https://ec.europa.eu/eurostat/databrowser/view/tour_occ_nim/default/table" target="_blank" rel="noopener">Eurostat tour_occ_nim</a>`,
  ecb: `<a href="https://frankfurter.dev/" target="_blank" rel="noopener">ECB reference rates via Frankfurter</a>`,
  airbnb: `<a href="https://insideairbnb.com/get-the-data/" target="_blank" rel="noopener">Inside Airbnb, Lisbon snapshot</a>`,
  bookings: `<a href="/grafana/d/cockpit-models" target="_blank" rel="noopener">booking stream</a>`,
  travelbi: `<a href="https://travelbi.turismodeportugal.pt/en/accommodation/revpar-and-adr/" target="_blank" rel="noopener">Turismo de Portugal, RevPAR and ADR</a>`,
};

const theme = {
  color: cats, backgroundColor: "transparent",
  textStyle: { color: C.muted, fontFamily: "IBM Plex Sans, system-ui, sans-serif", fontSize: 12 },
  title: { textStyle: { color: C.text, fontWeight: 500, fontSize: 13 }, subtextStyle: { color: C.muted, fontSize: 11 } },
  legend: { textStyle: { color: C.muted, fontSize: 11 }, itemWidth: 14, itemHeight: 8 },
  tooltip: { backgroundColor: "#fff", borderColor: C.rule, textStyle: { color: C.text, fontSize: 12 } },
  categoryAxis: { axisLine: { lineStyle: { color: C.rule } }, axisTick: { show: false }, axisLabel: { color: C.muted, fontSize: 11 }, splitLine: { show: false }, nameTextStyle: { color: C.muted, fontSize: 11 } },
  valueAxis: { axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: C.muted, fontSize: 11 }, splitLine: { lineStyle: { color: "#eef1f4", width: 1 } }, nameTextStyle: { color: C.muted, fontSize: 11 } },
  line: { symbol: "none", lineStyle: { width: 2 } }, bar: { barMaxWidth: 28 },
};

async function api(path) { const r = await fetch("/api/" + path); if (!r.ok) throw new Error(path + " " + r.status); return r.json(); }
function chart(id, option) { const el = document.getElementById(id); if (!el) return null; const c = echarts.init(el, "light-cockpit"); c.setOption(option); charts.push(c); return c; }
function clearCharts() { while (charts.length) charts.pop().dispose(); }
window.addEventListener("resize", () => charts.forEach(c => c.resize()));
const html = (s, ...v) => s.reduce((a, x, i) => a + x + (v[i] ?? ""), "");
function panel(span, id, title, sub, body, cls = "", src = "") {
  return html`<section class="panel span-${span}"><h2 id="${id}-title">${title}</h2><p class="sub" id="${id}-sub">${sub}</p>${body ?? `<div id="${id}" class="chart ${cls}"></div>`}<p class="src" id="${id}-src">${src}</p></section>`;
}
function head(title, lede) { return html`<div class="page-head"><h1>${title}</h1><p class="lede">${lede}</p></div>`; }
function setTitle(id, title, sub) { const t = document.getElementById(id + "-title"), s = document.getElementById(id + "-sub"); if (t && title != null) t.textContent = title; if (s && sub != null) s.textContent = sub; }
function fill(id, htmlText) { const el = document.getElementById(id); if (el) el.innerHTML = htmlText; }
let routeSeq = 0;
function setSrc(id, text) { const e = document.getElementById(id + "-src"); if (e) e.innerHTML = text; }
function groupBy(rows, key) { const o = {}; for (const r of rows) (o[r[key]] ??= []).push(r); return o; }
function addDays(iso, n) { const d = new Date(iso + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
function ban(k, v, d = "", cls = "") { return `<div class="ban"><div class="k">${k}</div><div class="v ${cls}">${v}</div><div class="d">${d}</div></div>`; }
function deltaText(cur, prev, goodUp = true, label = "vs same time last year") {
  if (prev == null || prev === 0 || cur == null) return "";
  const d = cur / prev - 1, good = goodUp ? d >= 0 : d <= 0;
  return `<span class="d ${good ? "good" : "bad"}">${d >= 0 ? "▲" : "▼"} ${signed(d)} ${label}</span>`;
}
const blueFill = (v, min, max) => { const t = Math.min(1, max === min ? 0 : (v - min) / (max - min)); return `rgba(47,111,219,${(0.06 + 0.7 * t).toFixed(2)})`; };
const redFill = (v, min, max) => { const t = Math.min(1, max === min ? 0 : (v - min) / (max - min)); return `rgba(214,69,69,${(0.06 + 0.7 * t).toFixed(2)})`; };
const fillText = (v, min, max) => Math.min(1, max === min ? 0 : (v - min) / (max - min)) > 0.6 ? "#fff" : C.text;

// sorted horizontal bars with a rich label, optional reference ticks and vertical reference lines
function hbar(id, rows, valueKey, labelKey, opts = {}) {
  rows = rows.slice().sort((a, b) => b[valueKey] - a[valueKey]);
  const color = opts.color || C.blue;
  const rich = { g: { color: C.green, fontSize: 11 }, b: { color: C.blue, fontSize: 11 }, r: { color: C.red, fontSize: 11 }, m: { color: C.muted, fontSize: 11 } };
  const series = [{ type: "bar", data: rows.map(r => ({ value: r[valueKey], itemStyle: { color: typeof color === "function" ? color(r) : color } })), barMaxWidth: 20, label: { show: true, position: "right", fontSize: 11, color: C.muted, rich, formatter: p => opts.format ? opts.format(rows[p.dataIndex]) : fmtK(p.value) } }];
  if (opts.refKey) series.push({ name: opts.refName || "last year", type: "scatter", symbol: "rect", symbolSize: [2, 18], data: rows.map(r => r[opts.refKey]), itemStyle: { color: C.text }, z: 3, tooltip: { valueFormatter: v => opts.tip ? opts.tip(v) : fmtK(v) } });
  if (opts.markLines) series[0].markLine = { symbol: "none", silent: true, lineStyle: { color: C.text, type: "dashed", width: 1 }, label: { color: C.text, fontSize: 11, position: "end", rotate: 0, distance: 6 }, data: opts.markLines.map(m => ({ xAxis: m.value, label: { formatter: m.label, position: m.position || "end" } })) };
  const pct = opts.xpct || false;
  chart(id, { tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: v => opts.tip ? opts.tip(v) : fmtK(v) }, legend: opts.refKey ? { top: 0, right: 0, data: [opts.refName || "last year"] } : undefined, grid: { left: opts.left || 130, right: opts.right || 90, top: opts.markLines ? 40 : opts.refKey ? 26 : 6, bottom: opts.xname ? 40 : 6 }, xAxis: { type: "value", min: 0, max: pct ? 1 : null, show: !!opts.xname, name: opts.xname, nameLocation: "middle", nameGap: 28, axisLabel: { formatter: v => pct ? fmtPct(v) : fmtK(v) } }, yAxis: { type: "category", inverse: true, data: rows.map(r => r[labelKey]), axisLabel: { fontSize: 11, color: C.text } }, series });
}
function spark(id, values, ref, color) {
  chart(id, { grid: { left: 2, right: 2, top: 4, bottom: 2 }, xAxis: { type: "category", show: false, data: values.map((_, i) => i) }, yAxis: { type: "value", show: false, min: 0 }, series: [{ type: "line", data: values, showSymbol: false, lineStyle: { width: 1.5, color }, areaStyle: { color, opacity: 0.08 }, markLine: ref == null ? undefined : { symbol: "none", silent: true, data: [{ yAxis: ref }], lineStyle: { color: C.gray, type: "dashed", width: 1 }, label: { show: false } } }] });
}
function highlightTable(rows, cols, cellKey, rowKey, colLabel, fmt) {
  const values = rows.map(r => r[cellKey]).filter(v => v != null), min = Math.min(...values), max = Math.max(...values);
  const byRow = groupBy(rows, rowKey);
  const shade = v => `rgba(47,111,219,${(0.08 + 0.72 * ((v - min) / (max - min || 1))).toFixed(2)})`;
  let out = `<table class="hl"><thead><tr><th></th>${cols.map(c => `<th class="num">${colLabel(c)}</th>`).join("")}</tr></thead><tbody>`;
  for (const [rk, list] of Object.entries(byRow)) {
    const m = Object.fromEntries(list.map(r => [r.col, r[cellKey]]));
    out += `<tr><th>${rk}</th>${cols.map(c => m[c] == null ? "<td></td>" : `<td style="background:${shade(m[c])};color:${fillText(m[c], min, max)}">${fmt(m[c])}</td>`).join("")}</tr>`;
  }
  return out + "</tbody></table>";
}
function recCard(r, isNew, compact = false) {
  let extra = "";
  if (r.dates && !compact) extra = `<table><thead><tr><th>Date</th><th class="num">On books</th><th class="num">Expected cancels</th><th class="num">Sell extra</th><th class="num">Walk risk</th></tr></thead><tbody>${r.dates.map(d => `<tr><td>${d.date}</td><td class="num">${d.rooms_otb} of ${d.capacity}</td><td class="num">${d.expected_cancels}</td><td class="num">${d.accept}</td><td class="num">${fmtPct(d.walk_risk)}</td></tr>`).join("")}</tbody></table>`;
  return html`<div class="rec${isNew ? " new" : ""}"><div class="pri"><b>${r.priority === 1 ? "Today" : r.priority === 2 ? "This week" : "Plan"}</b>${r.hotel}<br>${r.date}</div><div><h3>${r.title}</h3><p class="why">${r.detail}</p><p class="act">${r.action}</p>${extra}</div><div class="impact">${r.impact_eur ? fmtEur.format(r.impact_eur) : "—"}<small>${r.impact_eur ? "estimated impact" : "hygiene"}</small></div></div>`;
}

// morning brief
async function renderBrief() {
  const page = document.getElementById("page");
  page.innerHTML = head("Morning brief", "Rooms on the books for the next 30 days against the same point last year, where the booking curve says we will land, what the cancellation model expects to lose, and the two actions worth taking today.") + `<div id="brief"></div>`;
  const [b, pace, recs] = await Promise.all([api("rm/brief?days=30"), api("rm/pace?days=30"), api("rm/recommendations")]);
  let out = "";
  for (const h of hotels) {
    const x = b.hotels[h], key = h.split(" ")[0];
    out += html`<div class="hotel-row"><div class="hotel-head"><h2>${h}</h2><span class="label">${x.rooms_otb > x.rooms_stly ? "ahead of" : "behind"} last year by ${Math.abs(x.rooms_otb - x.rooms_stly)} room nights, forecast to finish at ${fmtPct(x.forecast_occupancy)} occupancy</span></div>
      <div class="bans">
        ${ban("Rooms on the books, 30 days", fmtInt.format(x.rooms_otb), deltaText(x.rooms_otb, x.rooms_stly))}
        ${ban("Occupancy on the books", fmtPct(x.occupancy_otb), `finished at ${fmtPct(x.occupancy_final_ly)} last year`)}
        ${ban("Rate on the books", fmtEur.format(x.adr_otb), deltaText(x.adr_otb, x.adr_stly))}
        ${ban("Forecast revenue, 30 days", fmtEur.format(x.forecast_revenue), deltaText(x.forecast_revenue, x.revenue_final_ly, true, "vs last year's final"))}
        ${ban("Expected cancellations", fmtInt.format(x.expected_cancels), `${fmtPct(x.expected_cancels / Math.max(x.rooms_otb, 1), 1)} of rooms on the books`, "red")}
        ${ban("Net pickup, last 7 days", (x.pickup_net >= 0 ? "+" : "") + fmtInt.format(x.pickup_net), `${x.pickup_gross} booked, ${x.pickup_cancels} cancelled (last year ${x.pickup_gross_stly} and ${x.pickup_cancels_stly})${x.biggest_cancel && x.biggest_cancel.nights >= 20 ? `; ${x.biggest_cancel.nights} of the cancelled nights are ${x.biggest_cancel.segment} bookings released on ${x.biggest_cancel.day.slice(5)}` : ""}`, x.pickup_net < 0 ? "red" : "green")}
      </div>
      <div class="grid"><section class="panel span-7"><h2 id="pace-${key}-title"></h2><p class="sub" id="pace-${key}-sub"></p><div id="pace-${key}" class="chart short"></div><p class="src">Source: ${sources.bookings}, bookings on the books</p></section>
      <section class="panel span-5"><h2>Occupancy for the next 30 days: today and predicted</h2><p class="sub">How full the hotel already is for the coming 30 nights, how full the booking curve expects it to end up, and where it ended last year.</p><div id="bullet-${key}" class="chart short"></div><p class="src">Source: booking curve forecast</p></section></div></div>`;
  }
  out += html`<section class="panel"><h2>What to do today</h2><p class="sub" id="recs-sub"></p><div id="recs-top"></div><p style="margin-top:14px"><a class="all-link" href="#recommendations">All recommendations and how each one is computed</a></p></section>`;
  fill("brief", out);
  for (const h of hotels) {
    const key = h.split(" ")[0], x = b.hotels[h];
    const ty = pace.this_year.filter(r => r.hotel === h), ly = pace.last_year.filter(r => r.hotel === h);
    const axis = [...new Set(ty.concat(ly).map(r => r.days_out))].sort((a, b) => b - a);
    const val = r => r.rooms_per_date ?? r.rooms;
    const tm = Object.fromEntries(ty.map(r => [r.days_out, val(r)])), lm = Object.fromEntries(ly.map(r => [r.days_out, val(r)]));
    setTitle("pace-" + key, `Pace: ${fmtInt.format(x.rooms_otb)} rooms on the books, ${signed(x.pace_index - 1)} against the same point last year`, "Average rooms on the books per stay date in the next 30 days, by days before arrival. The gap between the two lines is the pace.");
    chart("pace-" + key, { tooltip: { trigger: "axis" }, legend: { top: 0, right: 0 }, grid: { left: 48, right: 16, top: 30, bottom: 36 }, xAxis: { type: "category", data: axis, name: "days before arrival", nameLocation: "middle", nameGap: 24, axisLabel: { interval: 3 } }, yAxis: { type: "value", min: 0, max: Math.ceil(capacityOf(h) * 1.1 / 50) * 50, name: "rooms per stay date", nameLocation: "middle", nameGap: 36 },
      series: [{ name: "last year", type: "line", data: axis.map(d => lm[d] ?? null), lineStyle: { color: C.gray, width: 2 }, itemStyle: { color: C.gray } }, { name: "this year", type: "line", data: axis.map(d => tm[d] ?? null), lineStyle: { color: C.blue, width: 2.5 }, itemStyle: { color: C.blue } }, { name: "capacity", type: "line", data: axis.map(() => capacityOf(h)), lineStyle: { color: C.text, width: 1, type: "dashed" }, itemStyle: { color: C.text } }] });
    chart("bullet-" + key, { legend: { top: 0, left: 0, data: ["on the books today", "forecast at arrival", "last year's final"] }, grid: { left: 10, right: 20, top: 44, bottom: 24 }, xAxis: { type: "value", min: 0, max: 1, axisLabel: { formatter: v => fmtPct(v) } }, yAxis: { type: "category", data: ["occupancy"], show: false },
      series: [{ name: "on the books today", type: "bar", data: [x.occupancy_otb], barWidth: 34, itemStyle: { color: C.blueLight }, z: 1, label: { show: true, position: "insideLeft", formatter: fmtPct(x.occupancy_otb), color: C.text, fontSize: 11 } }, { name: "forecast at arrival", type: "bar", data: [x.forecast_occupancy], barWidth: 14, barGap: "-70%", itemStyle: { color: C.blue }, z: 2, label: { show: true, position: "right", formatter: fmtPct(x.forecast_occupancy), color: C.text, fontSize: 11 } }, { name: "last year's final", type: "scatter", symbol: "rect", symbolSize: [3, 44], data: [[x.occupancy_final_ly, 0]], itemStyle: { color: C.text }, z: 3, label: { show: true, position: "top", formatter: fmtPct(x.occupancy_final_ly), color: C.muted, fontSize: 11 } }] });
  }
  const top = recs.recommendations.slice(0, 2);
  setTitle("recs", null, recs.as_of ? `The two most urgent of ${recs.recommendations.length} recommendations computed from today's books, together worth about ${fmtEur.format(recs.total_impact_eur)}.` : "No recommendations computed yet.");
  fill("recs-top", top.map(r => recCard(r, recs.new.includes(r.rec_id), true)).join("") || `<div class="empty">Nothing to do today</div>`);
}

// recommendations
async function renderRecommendations() {
  const page = document.getElementById("page");
  page.innerHTML = head("Recommendations", "Rules a revenue manager would apply, evaluated every night on the live books, the cancellation scores, the booking curve and the demand feeds. Each card states its evidence and its assumption so the number can be challenged.") + `<div id="recs"></div>
  <section class="panel"><h2>How the rules work</h2><ul class="rules">
  <div id="rules-list"></div>
  <p class="src">Sources: cancellation model, booking curve forecast, channel effect, booking stream, live feeds</p></section>`;
  const recs = await api("rm/recommendations");
  fill("rules-list", `<ul class="rules">
  <li><b>Overbooking.</b> Dates forecast to sell out where expected cancellations exceed the extra rooms at a walk risk of at most 10%; one allowance per date, refreshed nightly.</li>
  <li><b>Rate hold.</b> Sell out dates within 45 days: close discounts and lift the rate 5%. No volume loss is assumed because demand already exceeds capacity; when the pickup elasticity of the Online TA channel is negative and significant, its volume loss is deducted.</li>
  <li><b>Stimulate demand.</b> Dates below 65% forecast occupancy while the pace index is below 0.9; half of the gap is assumed recoverable at the current rate.</li>
  <li><b>Confirm arrivals.</b> Bookings arriving within 14 days with a cancellation probability of at least 50% and worth at least ${fmtEur.format(300)}.</li>
  <li><b>Group release.</b> Group blocks of at least 10 room nights arriving in 30 to 60 days; the share of group cancellations historically known 30 days out is computed from the booking stream and shown on the risk page.</li>
  <li><b>Direct booking incentive.</b> When the Online TA share of the books is up 5 points or more on the same point last year; the incentive is capped at the expected loss avoided per room night from the measured effect of booking direct (channel effect).</li>
  <li><b>Agent deposits.</b> Agents with at least 30 resolved bookings in 180 days whose cancel rate, shrunk toward the hotel norm, sits 15 points above it. Agents booking non refundable are flagged as holds that inflate the books instead.</li>
  <li><b>Source markets.</b> School holidays in 14 to 75 days in a booker country whose bookings for that window trail the same point last year by 20% or more.</li>
  <li><b>Minimum stay.</b> Sell out dates where one night bookings hold 30% or more of the rooms.</li>
  <li><b>Quiet accounts.</b> Booking agents with at least 20 bookings the year before and none in the last 90 days; impact assumes a call recovers a quarter of that revenue.</li>
  <li><b>Guests who cancelled before.</b> Bookings on the books for the next 60 days from guests with a prior cancellation and no deposit, priced at the cancel rate such bookings showed in the last 12 months; impact assumes a deposit halves the loss.</li>
  <li><b>No engagement.</b> Flexible bookings made a month or more ahead with no special request, arriving in 14 to 60 days; impact assumes a pre arrival contact closes a fifth of the cancel rate gap to engaged bookings.</li>
  <li><b>Model and data hygiene.</b> Live predictions no better than the base rate, six or more drifted features, or a failed feed.</li>
  </ul>`);
  const groups = { 1: "Today", 2: "This week", 3: "Plan" };
  let out = `<p class="summary">${recs.recommendations.length} recommendations as of ${recs.as_of}, estimated impact <b style="color:${C.blue};font-weight:600">${fmtEur.format(recs.total_impact_eur)}</b>.${recs.previous_run ? " " + recs.new.length + " new since " + recs.previous_run + ". " + recs.resolved.length + " resolved." : ""}</p>`;
  for (const p of [1, 2, 3]) { const list = recs.recommendations.filter(r => r.priority === p); if (list.length) out += `<h2 style="margin-top:18px">${groups[p]}</h2>` + list.map(r => recCard(r, recs.new.includes(r.rec_id))).join(""); }
  if (recs.resolved.length) out += `<h2 style="margin-top:18px">Resolved since ${recs.previous_run}</h2>` + recs.resolved.map(r => recCard(r, false, true).replace('class="rec"', 'class="rec resolved"')).join("");
  fill("recs", out);
}

// pace and forecast
async function renderForecast() {
  const page = document.getElementById("page");
  page.innerHTML = head("Pace and forecast", "Per stay date: rooms already booked, the cancellations the model expects among them, the final rooms the booking curve predicts and the hotel's capacity. Below: the daily flow of new bookings against cancellations, the segment mix of the books against last year, and the seasonal pattern.") + `
  <div class="grid">${hotels.map(h => panel(12, "fc-" + h.split(" ")[0], h, "", null, "short", "Sources: booking curve forecast, cancellation model")).join("")}
  ${hotels.map(h => panel(6, "pu-" + h.split(" ")[0], "Rooms booked and cancelled per day, " + h, "New room nights and cancelled room nights per booking day over the last six weeks, for stays within 90 days.", null, "short", "Source: " + sources.bookings + ", bookings on the books")).join("")}
  ${hotels.map(h => panel(6, "mix-" + h.split(" ")[0], "Segment mix of the next 90 days, " + h, "Rooms on the books by market segment, compared with the same point last year.", null, "short", "Source: " + sources.bookings + ", bookings on the books")).join("")}
  ${hotels.map(h => panel(6, "sea-" + h.split(" ")[0], "Seasonality, " + h, "Average realised occupancy by month and weekday, all history.", `<div id="sea-${h.split(" ")[0]}"></div>`, "", "Source: " + sources.bookings + ", realised stays")).join("")}
  </div>`;
  const [fc, pu, mix, sea] = await Promise.all([api("rm/forecast?days=90"), api("rm/pickup?weeks=6"), api("rm/mix?days=90"), api("insights/seasonality")]);
  for (const h of hotels) {
    const key = h.split(" ")[0], rows = fc.filter(r => r.hotel === h);
    const sellout = rows.filter(r => r.forecast_occupancy >= 0.95).length, soft = rows.filter(r => r.forecast_occupancy < 0.65).length;
    setTitle("fc-" + key, `${h}: ${sellout} of the next 90 dates forecast to sell out, ${soft} soft`, "Rooms booked per stay date, the cancellations expected among them, the forecast of final rooms and the capacity. Bars above the capacity line are the overbooking the hotel accepts against the cancellations it expects.");
    chart("fc-" + key, { tooltip: { trigger: "axis" }, legend: { top: 0, right: 0 }, grid: { left: 48, right: 16, top: 30, bottom: 26 }, xAxis: { type: "category", data: rows.map(r => r.stay_date), axisLabel: { formatter: v => v.slice(5), interval: 6 } }, yAxis: { type: "value", min: 0, max: Math.max(Math.round(capacityOf(h) * 1.1), Math.ceil(Math.max(...rows.map(r => r.rooms_otb)) * 1.05 / 10) * 10), name: "rooms", nameLocation: "middle", nameGap: 34 },
      series: [{ name: "booked, expected to stay", type: "bar", stack: "a", data: rows.map(r => +(r.rooms_otb - r.expected_cancels).toFixed(1)), itemStyle: { color: C.blueLight }, barMaxWidth: 10 }, { name: "booked, expected to cancel", type: "bar", stack: "a", data: rows.map(r => r.expected_cancels), itemStyle: { color: C.red }, barMaxWidth: 10 }, { name: "forecast final rooms", type: "scatter", data: rows.map(r => r.forecast_rooms), symbolSize: 5, itemStyle: { color: C.blue } }, { name: "capacity", type: "line", data: rows.map(r => r.capacity), lineStyle: { color: C.text, width: 1 }, itemStyle: { color: C.text }, symbol: "none" }] });
    const p = pu.filter(r => r.hotel === h), net = p.reduce((a, r) => a + r.gross - r.cancels, 0);
    setTitle("pu-" + key, `${h}: ${net >= 0 ? "+" : ""}${fmtInt.format(net)} net room nights in six weeks, ${fmtInt.format(p.reduce((a, r) => a + r.gross, 0))} booked and ${fmtInt.format(p.reduce((a, r) => a + r.cancels, 0))} cancelled`, null);
    chart("pu-" + key, { tooltip: { trigger: "axis" }, legend: { top: 0, right: 0 }, grid: { left: 52, right: 12, top: 30, bottom: 26 }, xAxis: { type: "category", data: p.map(r => r.day), axisLabel: { formatter: v => v.slice(5), interval: 6 } }, yAxis: { type: "value", name: "room nights", nameLocation: "middle", nameGap: 38 }, series: [{ name: "booked", type: "bar", stack: "n", data: p.map(r => r.gross), itemStyle: { color: C.blue } }, { name: "cancelled", type: "bar", stack: "n", data: p.map(r => -r.cancels), itemStyle: { color: C.red } }] });
    const m = mix.this_year.filter(r => r.hotel === h), ml = Object.fromEntries(mix.last_year.filter(r => r.hotel === h).map(r => [r.market_segment, r.rooms]));
    const mixTot = m.reduce((a, r) => a + r.rooms, 0), lyTot = Object.values(ml).reduce((a, b) => a + b, 0), topMix = m.slice().sort((a, b) => b.rooms - a.rooms)[0];
    if (topMix) setTitle("mix-" + key, `${topMix.market_segment} holds ${fmtPct(topMix.rooms / mixTot)} of the next 90 days at the ${h}, ${signed(topMix.rooms / mixTot - (ml[topMix.market_segment] ?? 0) / Math.max(lyTot, 1))} on last year`, null);
    hbar("mix-" + key, m.map(r => ({ ...r, ly: ml[r.market_segment] ?? 0 })), "rooms", "market_segment", { refKey: "ly", refName: "same point last year", format: r => fmtInt.format(r.rooms) + " rooms at " + fmtEur.format(r.adr), right: 150, left: 100, xname: "rooms on the books" });
    const s = sea.filter(r => r.hotel === h).map(r => ({ row: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][r.dow - 1], col: r.month, occupancy: r.occupancy }));
    document.getElementById("sea-" + key).innerHTML = highlightTable(s, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], "occupancy", "row", c => ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"][c - 1], v => fmtPct(v));
  }
}

// cancellation risk
async function renderRisk() {
  const page = document.getElementById("page");
  page.innerHTML = head("Cancellation risk", "Every booking is scored when it arrives and the whole portfolio is rescored nightly with the time already survived as a feature. Revenue at risk is probability times booking value.") + `
  <div class="grid">
    ${panel(4, "ticker", "Arriving now", "The 30 latest bookings on the stream, sorted from the most to the least likely to cancel.", `<div class="ticker" id="ticker"></div>`, "", "Source: cancellation model")}
    ${panel(8, "byweek", "Revenue at risk by arrival week", "", `<div id="byweek-City" class="chart short"></div><div id="byweek-Resort" class="chart short"></div>`, "", "Source: cancellation model")}
    ${panel(5, "hazard", "When cancellations happen", "", null, "", "Source: " + sources.bookings + ", cancellation dates")}
    ${panel(7, "drivers", "Where the risk sits", "Expected loss on the books by level, labels show the loss and the mean cancellation probability.", `<div class="grid" style="gap:10px 20px"><div class="span-6"><div id="drv-deposit_type" class="chart short"></div></div><div class="span-6"><div id="drv-lead_bucket" class="chart short"></div></div><div class="span-6"><div id="drv-market_segment" class="chart short"></div></div><div class="span-6"><div id="drv-customer_type" class="chart short"></div></div></div>`, "", "Source: cancellation model")}
    ${panel(12, "top", "Bookings with the highest expected loss", "Top 40 by probability times value, arrivals after today. Shading follows the value in blue and the probability and expected loss in red.", `<div style="overflow:auto"><table id="top"></table></div>`, "", "Source: cancellation model")}
  </div>`;
  const [stream, byWeek, drivers, top, hazard] = await Promise.all([api("risk/stream?limit=30"), api("risk/by_week?weeks=16"), api("risk/drivers"), api("risk/top?limit=40"), api("insights/hazard")]);
  fill("ticker", stream.map(r => html`<div class="tick"><div class="p${r.p_cancel >= 0.5 ? " red" : ""}">${fmtPct(r.p_cancel)}</div><div><div>${r.hotel}, ${r.nights} nights from ${r.arrival_date}</div><div class="meta">${r.market_segment}, ${r.deposit_type}, ${r.country}, ${r.lead_time} days ahead</div></div><div class="val">${fmtEur.format(r.booking_value)}<small>at risk ${fmtEur.format(r.booking_value * r.p_cancel)}</small></div></div>`).join("") || `<div class="empty">No bookings scored yet</div>`);
  let totalRisk = 0, totalValue = 0;
  for (const r of byWeek) { totalRisk += r.revenue_at_risk; totalValue += r.value_on_books; }
  setTitle("byweek", `${fmtPct(totalValue ? totalRisk / totalValue : 0)} of the next 16 weeks' booked value is expected to cancel, ${fmtEur.format(totalRisk)} of ${fmtEur.format(totalValue)}`, "Value of the bookings on the books per arrival week and hotel, split into what is expected to stay and what is expected to cancel.");
  const wks = [...new Set(byWeek.map(r => r.arrival_week.slice(0, 10)))].sort();
  for (const h of hotels) {
    const m = Object.fromEntries(byWeek.filter(r => r.hotel === h).map(r => [r.arrival_week.slice(0, 10), r]));
    chart("byweek-" + h.split(" ")[0], { title: { text: h, left: 0, top: 0 }, legend: { top: 0, right: 0 }, tooltip: { trigger: "axis", valueFormatter: v => fmtEur.format(v) }, grid: { left: 52, right: 12, top: 30, bottom: 24 }, xAxis: { type: "category", data: wks, axisLabel: { formatter: v => v.slice(5) } }, yAxis: { type: "value", min: 0, axisLabel: { formatter: v => "€" + fmtK(v) } },
      series: [{ name: "expected to cancel", type: "bar", stack: "v", data: wks.map(w => m[w]?.revenue_at_risk ?? 0), itemStyle: { color: C.red } }, { name: "expected to stay", type: "bar", stack: "v", data: wks.map(w => m[w] ? m[w].value_on_books - m[w].revenue_at_risk : 0), itemStyle: { color: C.blueLight } }] });
  }
  const grid = hazard.grid, city = hazard.rows.filter(r => r.hotel === "City Hotel");
  const groups30 = city.find(r => r.market_segment === "Groups")?.t30, ota30 = city.find(r => r.market_segment === "Online TA")?.t30;
  setTitle("hazard", `At 30 days out, ${fmtPct(groups30)} of group cancellations are already known but only ${fmtPct(ota30)} of Online TA ones`, "City Hotel, bookings made at least 30 days ahead: share of the cancellations that will ever happen, by days before arrival.");
  chart("hazard", { tooltip: { trigger: "axis", valueFormatter: v => fmtPct(v) }, legend: { top: 0, right: 0 }, grid: { left: 48, right: 16, top: 34, bottom: 36 }, xAxis: { type: "category", data: grid, name: "days before arrival", nameLocation: "middle", nameGap: 24 }, yAxis: { type: "value", min: 0, max: 1, name: "share of cancellations known", nameLocation: "middle", nameGap: 36, axisLabel: { formatter: v => fmtPct(v) } },
    series: city.map((r, i) => ({ name: r.market_segment, type: "line", data: grid.map(g => r["t" + g]), lineStyle: { width: 2, color: reds[i % reds.length] }, itemStyle: { color: reds[i % reds.length] } })) });
  for (const [k, title] of [["deposit_type", "By deposit type"], ["lead_bucket", "By lead time in days"], ["market_segment", "By market segment"], ["customer_type", "By customer type"]]) {
    const rows = drivers[k].slice(0, 6);
    const el = document.getElementById("drv-" + k); el.insertAdjacentHTML("beforebegin", `<p class="sub" style="margin:0 0 2px">${title}</p>`);
    hbar("drv-" + k, rows, "revenue_at_risk", "level", { color: C.red, format: r => "{r|" + fmtEur.format(r.revenue_at_risk) + "}{m|, " + fmtPct(r.avg_p_cancel) + "}", right: 130, left: 100, tip: v => fmtEur.format(v) });
  }
  const robust = arr => { const sorted = arr.slice().sort((a, b) => a - b); const rest = sorted.length > 3 ? sorted.slice(0, -1) : sorted; return [rest[0], rest[rest.length - 1]]; };
  const [lmin, lmax] = robust(top.map(r => r.expected_loss)), [vmin, vmax] = robust(top.map(r => r.booking_value)), [pmn, pmx] = robust(top.map(r => r.p_cancel));
  fill("top", `<thead><tr><th>Hotel</th><th>Arrival</th><th class="num">Lead</th><th class="num">Nights</th><th>Segment</th><th>Deposit</th><th>Country</th><th class="num">Rate</th><th class="num">Value</th><th class="num">P(cancel)</th><th class="num">Expected loss</th></tr></thead><tbody>` + top.map(r => `<tr><td>${r.hotel}</td><td>${r.arrival_date}</td><td class="num">${r.lead_time}</td><td class="num">${r.nights}</td><td>${r.market_segment}</td><td>${r.deposit_type}</td><td>${r.country}</td><td class="num">${fmtEur.format(r.adr)}</td><td class="num fill" style="background:${blueFill(r.booking_value, vmin, vmax)};color:${fillText(r.booking_value, vmin, vmax)}">${fmtEur.format(r.booking_value)}</td><td class="num fill" style="background:${redFill(r.p_cancel, pmn, pmx)};color:${fillText(r.p_cancel, pmn, pmx)}">${fmtPct(r.p_cancel, 1)}</td><td class="num fill" style="background:${redFill(r.expected_loss, lmin, lmax)};color:${fillText(r.expected_loss, lmin, lmax)}">${fmtEur.format(r.expected_loss)}</td></tr>`).join("") + "</tbody>");
}

// demand signals

// readable name of a forecast method and, per hotel, the chosen method with its backtest error
const methodNames = { seasonal_naive: "seasonal naive", seasonal_naive_growth: "seasonal naive with year on year growth", ets: "damped exponential smoothing", fourier_arima: "Fourier regression with ARIMA errors", pickup: "the pickup model", gbm_direct: "a boosted model on last year's week, the recent level and the rooms on the books", ensemble: "the mean of all candidates", mean_ets_pickup: "the mean of smoothing and pickup" };
function forecastMethodText(f) {
  const parts = Object.keys(f).map(h => {
    const m = methodNames[f[h].method] || (f[h].method || "the demand forecast model").replace(/_/g, " ");
    const err = f[h].mape_backtest != null ? `, backtest error ${fmtPct(f[h].mape_backtest, 1)}` : "";
    return `${m} at the ${h}${err}`;
  });
  const same = Object.values(f).every(x => x.method === Object.values(f)[0].method);
  if (same && parts.length > 1) return `${methodNames[Object.values(f)[0].method] || Object.values(f)[0].method} at both hotels, chosen on a rolling backtest (error ${Object.keys(f).map(h => `${fmtPct(f[h].mape_backtest, 1)} at the ${h}`).join(", ")})`;
  return parts.join(" and ");
}

async function renderDemand() {
  const page = document.getElementById("page");
  page.innerHTML = head("Demand signals", "Free live feeds that lead or explain demand: attention to the Lisbon Wikipedia article per language edition, weather at both hotels, holidays in the source markets, official hotel nights and the euro against booker currencies.") + `
  <div class="grid">
    ${panel(8, "pv", "Wikipedia attention by source market", "", `<div class="spark-grid" id="pv"></div>`, "", "Source: " + sources.wikimedia)}
    ${panel(4, "markets", "Source markets on the books", "Booker country of bookings arriving in the next 90 days.", null, "", "Source: " + sources.bookings + ", bookings on the books")}
    ${panel(12, "fc", "Arrivals forecast", "", null, "", "Source: demand forecast")}
    ${panel(6, "wx", "Weather at the two hotels", "Daily maximum temperature for the next 14 days; blue tiles mean rain.", `<div id="wx-city"></div><p class="sub" style="margin-top:10px">Algarve resort</p><div id="wx-resort"></div>`, "", "Source: " + sources.openmeteo)}
    ${panel(6, "hol", "Holidays in the next 90 days", "Public holidays in the top booker countries and school holidays where available. Deeper shading marks the countries that book more rooms with us.", `<div class="holiday-list" id="hol"></div>`, "", "Source: " + sources.holidays)}
    ${panel(6, "eu", "Hotel nights per month, Eurostat", "Millions of nights spent in hotels and similar accommodation per country, with a nowcast of the months Portugal has not published yet.", null, "", "Source: " + sources.eurostat + "; Eurostat nowcast")}
    ${panel(6, "fx", "Euro against booker currencies", "Change in the ECB reference rate since one year ago. Positive means the euro buys more of that currency, so Portugal is dearer for that market.", null, "", "Source: " + sources.ecb)}
    ${panel(12, "adr", "Hotel rates against the official regional rate", "", null, "short", "Sources: " + sources.travelbi + ", four star hotels by region; " + sources.bookings + ", realised rate per stayed room night")}
  </div>`;
  const [pv, markets, wx, hol, eu, fx, fc, adr] = await Promise.all([api("demand/pageviews_weekly?weeks=52"), api("demand/source_markets?days=90"), api("demand/weather?past=0"), api("demand/holidays?days=90"), api("demand/eurostat"), api("demand/fx?days=365"), api("demand/forecast"), api("demand/adr")]);
  const langName = { "en.wikipedia": "English", "de.wikipedia": "German", "fr.wikipedia": "French", "es.wikipedia": "Spanish", "it.wikipedia": "Italian", "pt.wikipedia": "Portuguese", "nl.wikipedia": "Dutch", "pl.wikipedia": "Polish", "sv.wikipedia": "Swedish", "ja.wikipedia": "Japanese", "zh.wikipedia": "Chinese", "ru.wikipedia": "Russian" };
  const byLang = groupBy(pv, "project"), cutoff = addDays(new Date().toISOString().slice(0, 10), -365);
  const allWeeks = [...new Set(pv.map(r => r.week.slice(0, 10)))].filter(w => w >= cutoff).sort().slice(0, -1);
  const items = [];
  for (const [proj, rows] of Object.entries(byLang)) {
    const m = Object.fromEntries(rows.map(r => [r.week.slice(0, 10), r.views])), vals = allWeeks.map(w => m[w] ?? null), clean = vals.filter(v => v != null);
    if (clean.length < 20) continue;
    const med = clean.slice().sort((a, b) => a - b)[Math.floor(clean.length / 2)], last4 = clean.slice(-4).reduce((a, b) => a + b, 0) / 4;
    items.push({ proj, name: langName[proj] || proj, vals, med, change: last4 / med - 1 });
  }
  items.sort((a, b) => b.change - a.change);
  document.getElementById("pv-sub").className = "sub wide";
  setTitle("pv", `${items[0]?.name} readers are ${fmtPct(items[0]?.change)} above their usual week, ${items[items.length - 1]?.name} readers ${fmtPct(-items[items.length - 1]?.change)} below`, "Weekly views of the Lisbon article over the last 52 weeks, one panel per language edition; the dashed line is that language's median week and the figure compares the last four weeks with it.");
  fill("pv", items.map(it => `<div class="spark"><div class="t"><b>${it.name}</b><span class="${it.change >= 0 ? "d good" : "d bad"}">${signed(it.change)} vs median</span></div><div id="spk-${it.proj.split(".")[0]}" class="chart tiny"></div></div>`).join(""));
  items.forEach(it => spark("spk-" + it.proj.split(".")[0], it.vals, it.med, it.change >= 0 ? C.green : C.red));
  hbar("markets", markets.slice(0, 12), "bookings", "country", { left: 50, right: 60, format: r => fmtInt.format(r.bookings), xname: "bookings" });
  setTitle("markets", `${markets[0]?.country} sends ${fmtPct(markets[0]?.bookings / markets.reduce((a, r) => a + r.bookings, 0))} of bookings arriving in the next 90 days`, null);
  if (fc.forecast) {
    const act = groupBy(fc.actual || [], "hotel"), weeksAll = [...new Set((fc.actual || []).map(r => r.week.slice(0, 10)))].sort().slice(0, -1);
    const fcWeeks = fc.forecast["City Hotel"].weeks, axis = weeksAll.concat(fcWeeks), s = [];
    for (const h of hotels) {
      const m = Object.fromEntries((act[h] || []).map(r => [r.week.slice(0, 10), r.arrivals])), f = fc.forecast[h], fm = Object.fromEntries(f.weeks.map((w, i) => [w, f.arrivals[i]]));
      const col = h === "City Hotel" ? C.blue : cats[2];
      s.push({ name: h + ", realised", type: "line", data: axis.map(w => m[w] ?? null), lineStyle: { color: col, width: 2 }, itemStyle: { color: col } });
      s.push({ name: h + ", forecast", type: "line", data: axis.map(w => fm[w] ?? null), lineStyle: { color: col, width: 2, type: "dashed" }, itemStyle: { color: col }, symbol: "circle", symbolSize: 4 });
    }
    setTitle("fc", `Next ${fc.horizon_weeks} weeks: ${fmtInt.format(fc.forecast["City Hotel"].arrivals.reduce((a, b) => a + b, 0))} arrivals at the City Hotel and ${fmtInt.format(fc.forecast["Resort Hotel"].arrivals.reduce((a, b) => a + b, 0))} at the Resort Hotel`, `Stayed arrivals per week for the City Hotel and the Resort Hotel: realised for the last 26 weeks and forecast for the next ${fc.horizon_weeks} on the shaded ground by ${forecastMethodText(fc.forecast)}.`);
    chart("fc", { tooltip: { trigger: "axis" }, legend: { top: 0, right: 0 }, grid: { left: 56, right: 16, top: 30, bottom: 28 }, xAxis: { type: "category", data: axis, boundaryGap: false, axisLabel: { formatter: v => v.slice(5) } }, yAxis: { type: "value", min: 0, name: "stayed arrivals per week", nameLocation: "middle", nameGap: 40 }, series: s.concat([{ type: "line", data: [], markArea: { silent: true, itemStyle: { color: "#f4f6f8" }, data: [[{ xAxis: fcWeeks[0] }, { xAxis: fcWeeks[fcWeeks.length - 1] }]] } }]) });
  }
  const wxBlock = loc => wx.filter(r => r.location === loc && r.kind === "forecast").slice(0, 14).map(r => `<div class="${r.precip_mm >= 1 ? "wet" : ""}"><b>${Math.round(r.temp_max)}°</b>${r.date.slice(5)}<br>${r.precip_mm >= 0.5 ? r.precip_mm.toFixed(0) + " mm" : "dry"}</div>`).join("");
  fill("wx-city", `<p class="sub" style="margin:0 0 6px">Lisbon city</p><div class="wx">${wxBlock("city")}</div>`);
  fill("wx-resort", `<div class="wx">${wxBlock("resort")}</div>`);
  const rainy = wx.filter(r => r.location === "city" && r.kind === "forecast" && r.precip_mm >= 1).length;
  setTitle("wx", rainy ? `${rainy} rainy days in Lisbon's 16 day forecast` : "Dry Lisbon forecast for the next 16 days", null);
  const iso2to3 = { PT: "PRT", GB: "GBR", FR: "FRA", ES: "ESP", DE: "DEU", IT: "ITA", IE: "IRL", BE: "BEL", BR: "BRA", NL: "NLD", US: "USA", CH: "CHE", CN: "CHN", AT: "AUT", SE: "SWE" };
  const mk = Object.fromEntries(markets.map(r => [r.country, r.bookings])), mkMax = Math.max(...markets.map(r => r.bookings), 1);
  fill("hol", hol.slice(0, 30).map(r => { const b = mk[iso2to3[r.country]] || 0; return `<div style="background:rgba(47,111,219,${(0.75 * b / mkMax).toFixed(2)});color:${b / mkMax > 0.6 ? "#fff" : C.text}"><span class="c" style="color:${b / mkMax > 0.6 ? "#fff" : C.muted}">${r.start_date}${r.days > 1 ? " to " + r.end_date : ""}</span><span>${r.country}</span><span>${r.name}${r.kind === "school" ? " (school)" : ""}</span></div>`; }).join("") || `<div class="empty">No holidays loaded</div>`);
  const eg = groupBy(eu, "geo"), euNames = { PT: "Portugal", ES: "Spain", IT: "Italy", FR: "France", EL: "Greece", HR: "Croatia" };
  let emonths = [...new Set(eu.map(r => r.month.slice(0, 7)))].sort().slice(-36);
  const es = [], pt = eg["PT"] || [], lastPt = pt[pt.length - 1], prevPt = pt.find(r => r.month.slice(0, 7) === addDays(lastPt?.month.slice(0, 10) ?? "2020-01-01", -365).slice(0, 7));
  const nc = fc.nowcast_live && fc.nowcast_live.months && fc.nowcast_live.months.length ? fc.nowcast_live : null;
  if (nc) nc.months.map(m => m.slice(0, 7)).forEach(m => { if (!emonths.includes(m)) emonths.push(m); });
  Object.entries(eg).forEach(([geo, rows], i) => { const m = Object.fromEntries(rows.map(r => [r.month.slice(0, 7), r.nights])); const col = geo === "PT" ? C.blue : cats[(i % 5) + 1]; es.push({ name: euNames[geo] || geo, type: "line", data: emonths.map(x => m[x] != null ? +(m[x] / 1e6).toFixed(2) : null), lineStyle: { width: geo === "PT" ? 2.5 : 1.5, color: col }, itemStyle: { color: col } }); });
  if (nc) {
    const nm = Object.fromEntries(nc.months.map((m, i) => [m.slice(0, 7), +(nc.nights[i] / 1e6).toFixed(2)])), li = emonths.indexOf(lastPt.month.slice(0, 7));
    es.push({ name: "Portugal nowcast", type: "line", data: emonths.map((x, i) => i === li ? +(lastPt.nights / 1e6).toFixed(2) : nm[x] ?? null), lineStyle: { color: C.blue, width: 2, type: "dashed" }, itemStyle: { color: C.blue }, symbol: "circle", symbolSize: 4 });
    setTitle("eu", null, `Millions of nights spent in hotels and similar accommodation per country. Portugal has published up to ${nc.last_published.slice(0, 7)}; the dashed part is a nowcast of ${nc.months.map(m => m.slice(0, 7)).join(" and ")} from the same months last year and the recent growth trend.`);
    setSrc("eu", "Wikipedia attention was tested as a leading signal for the nowcast and rejected; see the models dashboard in Grafana. Source: " + sources.eurostat + "; Eurostat nowcast");
  } else setTitle("eu", null, "Millions of nights spent in hotels and similar accommodation per country; every month is published, no nowcast needed.");
  setTitle("eu", lastPt && prevPt ? `Portugal hotel nights ${lastPt.month.slice(0, 7)}: ${(lastPt.nights / 1e6).toFixed(2)}M, ${signed(lastPt.nights / prevPt.nights - 1, 1)} on the same month a year earlier` : "Hotel nights per month", null);
  chart("eu", { tooltip: { trigger: "axis", valueFormatter: v => v == null ? "" : v + "M" }, legend: { top: 0, right: 0, type: "scroll" }, grid: { left: 56, right: 16, top: 34, bottom: 28 }, xAxis: { type: "category", data: emonths, axisLabel: { interval: 0, formatter: v => v.slice(5, 7) === "01" ? v.slice(0, 4) : v.slice(5, 7) === "07" ? "Jul" : "" }, axisTick: { interval: (i, v) => v.slice(5, 7) === "01" || v.slice(5, 7) === "07" } }, yAxis: { type: "value", min: 0, name: "hotel nights, millions", nameLocation: "middle", nameGap: 40, axisLabel: { formatter: v => v + "M" } }, series: es });
  const fg = groupBy(fx, "currency"), fdates = [...new Set(fx.map(r => r.date))].sort(), fs = [];
  Object.entries(fg).forEach(([cur, rows], i) => { const m = Object.fromEntries(rows.map(r => [r.date, r.rate])), first = rows[0]?.rate || 1, main = cur === "GBP" || cur === "USD", col = cur === "GBP" ? C.blue : cur === "USD" ? C.text : cats[(i % 4) + 1]; fs.push({ name: cur, type: "line", data: fdates.map(d => m[d] != null ? +((m[d] / first - 1) * 100).toFixed(2) : null), connectNulls: true, lineStyle: { width: main ? 2.5 : 1.2, color: col, opacity: main ? 1 : 0.45 }, itemStyle: { color: col }, endLabel: { show: true, formatter: cur, fontSize: 11, color: col, opacity: main ? 1 : 0.6 } }); });
  const chg = rows => rows?.length ? ((rows[rows.length - 1].rate / rows[0].rate - 1) * 100).toFixed(1) : "0";
  setTitle("fx", `Euro moved ${chg(fg["GBP"])}% against the pound and ${chg(fg["USD"])}% against the dollar over the year`, null);
  const amonths = [...new Set([...adr.official.map(r => r.month.slice(0, 7)), ...adr.hotels.map(r => r.month.slice(0, 7))])].sort();
  const aser = [];
  for (const [h, region] of Object.entries(adr.regions)) {
    const off = Object.fromEntries(adr.official.filter(r => r.region === region).map(r => [r.month.slice(0, 7), r.adr])), our = Object.fromEntries(adr.hotels.filter(r => r.hotel === h).map(r => [r.month.slice(0, 7), r.adr]));
    aser.push({ name: h, type: "line", data: amonths.map(m => our[m] == null ? null : Math.round(our[m])), lineStyle: { color: hotelColor(h), width: 2.5 }, itemStyle: { color: hotelColor(h) }, symbol: "none", connectNulls: false });
    aser.push({ name: region + ", four star hotels", type: "line", data: amonths.map(m => off[m] == null ? null : Math.round(off[m])), lineStyle: { color: hotelColor(h), width: 1.5, type: "dashed" }, itemStyle: { color: hotelColor(h) }, symbol: "none", connectNulls: false });
  }
  const lastOff = m => { const o = adr.official.filter(r => r.month.slice(0, 7) === m); return o.length ? o : null; };
  const lastMonth = [...amonths].reverse().find(m => lastOff(m) && adr.hotels.some(r => r.month.slice(0, 7) === m));
  if (lastMonth) {
    const ratio = Object.entries(adr.regions).map(([h, region]) => { const o = adr.official.find(r => r.region === region && r.month.slice(0, 7) === lastMonth), u = adr.hotels.find(r => r.hotel === h && r.month.slice(0, 7) === lastMonth); return o && u ? `${h} ${signed(u.adr / o.adr - 1)}` : null; }).filter(Boolean);
    setTitle("adr", `In ${lastMonth} our rates sat ${ratio.join(" and ")} against the four star average of their region`, "Realised rate per stayed room night of each hotel against the official average daily rate of four star hotels in Grande Lisboa and the Algarve, monthly since 2019. The stream's rates follow the official series month by month, the level comes from the source hotels.");
  }
  chart("adr", { tooltip: { trigger: "axis", valueFormatter: v => v == null ? "" : fmtEur.format(v) }, legend: { top: 0, right: 0, type: "scroll" }, grid: { left: 56, right: 16, top: 34, bottom: 28 }, xAxis: { type: "category", data: amonths, axisLabel: { interval: 0, formatter: v => v.slice(5, 7) === "01" ? v.slice(0, 4) : v.slice(5, 7) === "07" ? "Jul" : "" }, axisTick: { interval: (i, v) => v.slice(5, 7) === "01" || v.slice(5, 7) === "07" } }, yAxis: { type: "value", min: 0, name: "euros per room night", nameLocation: "middle", nameGap: 40 }, series: aser });
  chart("fx", { tooltip: { trigger: "axis", valueFormatter: v => v == null ? "" : v + "%" }, legend: { top: 0, right: 0 }, grid: { left: 52, right: 44, top: 34, bottom: 28 }, xAxis: { type: "category", data: fdates, boundaryGap: false, axisLabel: { formatter: v => v.slice(0, 7), interval: 60 } }, yAxis: { type: "value", name: "change since a year ago", nameLocation: "middle", nameGap: 40, axisLabel: { formatter: v => v + "%" } }, series: fs });
}

// lisbon market
async function renderMarket() {
  const page = document.getElementById("page");
  page.innerHTML = head("Lisbon rental market", "Context from the short term rental market the two hotels compete with, not the source markets of our guests (those are on the guests page): Inside Airbnb's Lisbon snapshot, what drives listing prices, how occupancy responds to price, and what guests praise or complain about in recent reviews.") + `
  <div class="grid">
    <section class="panel span-12"><div class="bans row" id="mk-bans"></div><p class="src">Source: ${sources.airbnb}</p></section>
    ${panel(7, "nb", "Neighbourhood price ladder", "Median nightly price of Airbnb listings per neighbourhood with at least 100 listings; labels show the share of the next year already booked. Dashed lines: the rate on the books at our two hotels.", null, "tall", "Sources: " + sources.airbnb + "; bookings on the books")}
    ${panel(5, "resp", "Occupancy responds to price", "", null, "tall", "Source: Airbnb hedonic price model")}
    ${panel(7, "asp", "What guests complain about", "", null, "tall", "Source: keyword aspects in recent English reviews, " + sources.airbnb)}
    ${panel(5, "rt", "Room types on offer", "Listings and median nightly price by room type.", null, "tall", "Source: " + sources.airbnb)}
  </div>`;
  const [ov, asp, brief] = await Promise.all([api("market/overview"), api("market/aspects"), api("rm/brief?days=30")]);
  const s = ov.summary, a = ov.airbnb || {};
  if (ov.snapshot_date) document.querySelectorAll(".src").forEach(e => { e.innerHTML = e.innerHTML.replace("Lisbon snapshot</a>", "Lisbon snapshot of " + new Date(ov.snapshot_date + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }) + "</a>"); });
  fill("mk-bans", ban("Active listings", fmtInt.format(s.listings), "priced " + fmtEur.format(20) + " to " + fmtEur.format(1500) + " per night") + ban("Median nightly price", fmtEur.format(s.median_price), `${fmtPct(s.entire_home_share)} entire homes`) + ban("Average guest rating", s.rating, "out of 5") + ban("Recent English reviews read", fmtInt.format(asp.reviews || 0), asp.from_date ? "since " + new Date(asp.from_date + "T00:00:00Z").toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" }) : ""));
  const nbs = (a.neighbourhoods || []).slice(0, 18);
  hbar("nb", nbs, "median_price", "neighbourhood_cleansed", { left: 200, right: 130, format: r => fmtEur.format(r.median_price) + ", " + fmtPct(r.booked_share) + " booked", tip: v => fmtEur.format(v), xname: "median nightly price", markLines: [{ value: brief.hotels["City Hotel"].adr_otb, label: "City Hotel\n" + fmtEur.format(brief.hotels["City Hotel"].adr_otb), position: "start" }, { value: brief.hotels["Resort Hotel"].adr_otb, label: "Resort Hotel\n" + fmtEur.format(brief.hotels["Resort Hotel"].adr_otb), position: "start" }] });
  const curve = a.price_curve || [];
  if (curve.length) {
    const lo = curve[0], hi = curve[curve.length - 1];
    setTitle("resp", `Listings priced well under their model value have ${fmtPct(lo.booked_share - hi.booked_share, 1)} more of the coming year booked than those priced well over`, "Each listing's price is compared with the value a model predicts from its size, location, reviews and amenities. Listings priced above their model value have a smaller share of the next 365 days already booked; the effect holds when comparing listings within the same neighbourhood and room type.");
    chart("resp", { tooltip: { trigger: "axis", valueFormatter: v => fmtPct(v, 1) }, grid: { left: 60, right: 16, top: 12, bottom: 40 }, xAxis: { type: "category", data: curve.map(r => signed(r.price_gap)), name: "price against model value", nameLocation: "middle", nameGap: 26 }, yAxis: { type: "value", min: v => Math.floor(v.min * 50) / 50, max: v => Math.ceil(v.max * 50) / 50, name: "share of next 365 days booked", nameLocation: "middle", nameGap: 44, axisLabel: { formatter: v => fmtPct(v) } }, series: [{ type: "line", data: curve.map(r => r.booked_share), symbol: "circle", symbolSize: 6, lineStyle: { color: C.blue }, itemStyle: { color: C.blue } }] });
  }
  if (asp.aspects) {
    const rows = asp.aspects.slice().sort((x, y) => y.negative_share - x.negative_share);
    setTitle("asp", `${rows[0].aspect} and ${rows[1].aspect} draw the most complaints: ${fmtPct(rows[0].negative_share)} and ${fmtPct(rows[1].negative_share)} of their mentions are negative`, "Share of review sentences mentioning each aspect that use negative wording; labels also show how many reviews mention the aspect. Hotels with lifts, quiet rooms and good bathrooms have a clear edge over the rental market.");
    hbar("asp", rows, "negative_share", "aspect", { color: C.red, left: 90, right: 150, format: r => "{r|" + fmtPct(r.negative_share) + " negative}{m|, in " + fmtPct(r.mention_share) + " of reviews}", tip: v => fmtPct(v), xname: "share of mentions that are negative", xpct: true });
  }
  hbar("rt", ov.room_types || [], "listings", "room_type", { left: 120, right: 130, format: r => fmtInt.format(r.listings) + " listings, " + fmtEur.format(r.median_price), xname: "listings" });
  setTitle("rt", `${ov.room_types?.[0]?.room_type} listings dominate at a median of ${fmtEur.format(ov.room_types?.[0]?.median_price)} per night`, null);
}

// agents and channels
async function renderAgents() {
  const page = document.getElementById("page");
  page.innerHTML = head("Agents and channels", "How bookings reach the hotel: which market segments bring the revenue and which cancel, how much of the business sits with a few booking agents, which agents cancel above the norm or have gone quiet, and how bookings of each channel survive after they are made. Everything is computed on the last 12 months of the stream and refreshes with it.") + `
  <section class="panel"><div class="bans row" id="a-bans"></div><p class="src">Source: ${sources.bookings}, last 12 months against the 12 months before, outcomes known today</p></section>
  <div class="grid" style="margin-top:22px">
    ${panel(6, "seg", "Market segments", "", null, "tall", "Source: " + sources.bookings + ", arrivals of the last 12 months with a known outcome")}
    ${panel(6, "agents", "Booking agents, stays of the last 12 months", "", null, "tall", "Source: " + sources.bookings + ", arrivals of the last 12 months with a known outcome, by agent")}
    ${panel(6, "sur", "How bookings survive after they are made", "", null, "", "Source: " + sources.bookings + ", bookings made 6 to 18 months ago, outcomes known today")}
    ${panel(6, "acc", "Booking agents that went quiet", "", `<div class="scroll-table"><table id="acc"></table></div>`, "", "Source: " + sources.bookings + ", bookings by agent, last 12 months against the 12 months before")}
    ${panel(6, "coh", "How each month's bookings turned out", "", null, "", "Source: " + sources.bookings + ", booked value by month of booking")}
    ${panel(6, "segtab", "Cancellations and no shows by segment and hotel", "Share of bookings that cancelled and share that did not show, by market segment and hotel, arrivals of the last 12 months with a known outcome. Deeper red means more.", `<div style="overflow:auto"><table id="segtab"></table></div>`, "", "Source: " + sources.bookings + ", bookings made in the last 12 months with a known outcome")}
  </div>`;
  const [seg, ag, acc, coh] = await Promise.all([api("guests/segments"), api("insights/agents"), api("guests/accounts"), api("guests/cohorts")]);
  const segs = Object.fromEntries(acc.segments.map(s => [s.segment, s])), active = ["champion", "loyal", "occasional", "new", "at risk"];
  const agentRev = active.reduce((a, s) => a + (segs[s]?.revenue || 0), 0), agentRevLy = active.reduce((a, s) => a + (segs[s]?.prior_revenue || 0), 0);
  const city = ag.hotels["City Hotel"] || {}, resort = ag.hotels["Resort Hotel"] || {}, risk = acc.at_risk;
  const onlineRev = seg.filter(r => r.market_segment === "Online TA").reduce((a, r) => a + r.realised_revenue, 0), offlineRev = seg.filter(r => r.market_segment === "Offline TA/TO").reduce((a, r) => a + r.realised_revenue, 0);
  fill("a-bans", ban("Realised revenue through booking agents and agencies, stays of the last 12 months", fmtEur.format(agentRev), `online agencies ${fmtEur.format(onlineRev)}, offline agents and operators ${fmtEur.format(offlineRev)}; ${deltaText(agentRev, agentRevLy, true, "vs the 12 months before")}`)
    + ban("Active agents", fmtInt.format(acc.summary.active), segs.champion ? `${fmtInt.format(segs.champion.accounts)} champions bring ${fmtPct(segs.champion.revenue / Math.max(agentRev, 1))} of it` : "")
    + ban("Top 5 agents' share", fmtPct(city.top5_share), `of City Hotel bookings; ${fmtPct(resort.top5_share)} at the Resort Hotel`)
    + ban("Agents gone quiet", fmtInt.format(risk.length), risk.length ? `${fmtEur.format(risk.reduce((a, r) => a + r.prior_revenue, 0))} of revenue the year before, silent for ${acc.summary.dormant_days} days` : "every agent with volume last year has booked recently", risk.length ? "red" : ""));
  const bySeg = {};
  for (const r of seg) { const s = bySeg[r.market_segment] ??= { revenue: 0, bookings: 0, canceled: 0 }; s.revenue += r.realised_revenue; s.bookings += r.bookings; s.canceled += r.bookings * r.cancel_rate; }
  const segRows = Object.entries(bySeg).map(([k, v]) => ({ level: k, revenue: v.revenue, rate: v.canceled / v.bookings })), tot = segRows.reduce((a, r) => a + r.revenue, 0), topSeg = segRows.slice().sort((a, b) => b.revenue - a.revenue)[0];
  if (topSeg) setTitle("seg", `${topSeg.level} brings ${fmtPct(topSeg.revenue / tot)} of realised revenue but cancels ${fmtPct(topSeg.rate)} of its bookings`, "Realised room revenue by market segment, both hotels, arrivals of the last 12 months with a known outcome, with the cancel rate of each segment.");
  hbar("seg", segRows, "revenue", "level", { left: 110, right: 170, format: r => "{b|" + fmtEur.format(r.revenue) + "}{r|, cancels " + fmtPct(r.rate) + "}", tip: v => fmtEur.format(v), xname: "realised revenue" });
  const segNames = [...new Set(seg.map(r => r.market_segment))], cell = (h, s) => seg.find(r => r.hotel === h && r.market_segment === s);
  const cvals = seg.map(r => r.cancel_rate), cmin = Math.min(...cvals), cmax = Math.max(...cvals), nvals = seg.map(r => r.no_show_rate), nmin = Math.min(...nvals), nmax = Math.max(...nvals);
  fill("segtab", `<thead><tr><th>Segment</th>${hotels.map(h => `<th class="num">${h}<br><span class="label">cancelled</span></th><th class="num">${h}<br><span class="label">no show</span></th>`).join("")}</tr></thead><tbody>` + segNames.map(sname => `<tr><td>${sname}</td>${hotels.map(h => { const c = cell(h, sname); return c ? `<td class="num fill" style="background:${redFill(c.cancel_rate, cmin, cmax)};color:${fillText(c.cancel_rate, cmin, cmax)}">${fmtPct(c.cancel_rate, 1)}</td><td class="num fill" style="background:${redFill(c.no_show_rate, nmin, nmax)};color:${fillText(c.no_show_rate, nmin, nmax)}">${fmtPct(c.no_show_rate, 1)}</td>` : "<td></td><td></td>"; }).join("")}</tr>`).join("") + "</tbody>");
  const agentTitle = city.top1_revenue_share >= 0.3 ? `Agent ${city.top_agent} brings ${fmtPct(city.top1_revenue_share)} of the City Hotel's agent revenue, the five largest agents ${fmtPct(city.top5_revenue_share)} together` : city.top5_revenue_share >= 0.5 ? `The five largest agents bring ${fmtPct(city.top5_revenue_share)} of the City Hotel's agent revenue, the largest (agent ${city.top_agent}) ${fmtPct(city.top1_revenue_share)}` : `Agent revenue at the City Hotel is spread out: the five largest agents bring only ${fmtPct(city.top5_revenue_share)}`;
  setTitle("agents", agentTitle, `Realised revenue per agent at the City Hotel for the arrivals of the last 12 months, the 15 largest of ${city.agents} agents with a known outcome; labels show each agent's cancel rate shrunk toward the hotel norm of ${fmtPct(city.prior_cancel_rate)}, red bars cancel at least 15 points above it.`);
  hbar("agents", ag.agents.filter(r => r.hotel === "City Hotel").slice(0, 15).map(r => ({ ...r, label: "agent " + r.agent })), "realised_revenue", "label", { left: 90, right: 170, color: r => r.shrunk_cancel_rate >= city.prior_cancel_rate + 0.15 ? C.red : C.blue, format: r => "{b|" + fmtEur.format(r.realised_revenue) + "}{r|, cancels " + fmtPct(r.shrunk_cancel_rate) + "}", tip: v => fmtEur.format(v), xname: "realised revenue" });
  const quietDays = acc.summary.dormant_days, minBook = acc.summary.at_risk_min_bookings;
  setTitle("acc", risk.length ? `${risk.length} agents with at least ${minBook} bookings the year before have not booked for ${quietDays} days, ${fmtEur.format(risk.reduce((a, r) => a + r.prior_revenue, 0))} of revenue at stake` : `Every agent with at least ${minBook} bookings the year before has booked in the last ${quietDays} days`, `Agents ranked by the revenue of the year before. Lifecycle of the ${fmtInt.format(acc.summary.active)} active agents: ${["champion", "loyal", "occasional", "new"].filter(s => segs[s]).map(s => `${fmtInt.format(segs[s].accounts)} ${s}`).join(", ")}.`);
  const smin = Math.min(...risk.map(r => r.recency_days), 0), smax = Math.max(...risk.map(r => r.recency_days), 1), rvmax = Math.max(...risk.map(r => r.revenue), 1), pvmax = Math.max(...risk.map(r => r.prior_revenue), 1);
  fill("acc", `<thead><tr><th>Agent</th><th class="num">Silent for</th><th class="num">Revenue, year before</th><th class="num">Revenue, last year</th><th class="num">Cancel rate</th><th class="num">Rate index</th><th class="num">Bookings, year before</th><th class="num">Bookings, last year</th></tr></thead><tbody>` + risk.map(r => `<tr><td>agent ${r.agent}</td><td class="num fill" style="background:${redFill(r.recency_days, smin, smax)};color:${fillText(r.recency_days, smin, smax)}">${r.recency_days} days</td><td class="num fill" style="background:${blueFill(r.prior_revenue, 0, pvmax)};color:${fillText(r.prior_revenue, 0, pvmax)}">${fmtEur.format(r.prior_revenue)}</td><td class="num fill" style="background:${blueFill(r.revenue, 0, rvmax)};color:${fillText(r.revenue, 0, rvmax)}">${fmtEur.format(r.revenue)}</td><td class="num">${r.cancel_rate == null ? "few cases" : fmtPct(r.cancel_rate)}</td><td class="num">${r.adr_index.toFixed(2)}</td><td class="num">${fmtInt.format(r.prior_bookings)}</td><td class="num">${fmtInt.format(r.bookings)}</td></tr>`).join("") + "</tbody>");
  const grid = coh.survival_grid, sv = coh.survival, ota = sv.find(r => r.market_segment === "Online TA"), direct = sv.find(r => r.market_segment === "Direct");
  setTitle("sur", ota && direct ? `Online TA bookings lose ${fmtPct(1 - ota.s30)} of their number within 30 days of booking, Direct bookings ${fmtPct(1 - direct.s30)}` : "How bookings survive after they are made", "Share of bookings not yet cancelled by days since the booking was made, per market segment; the drop at day 0 is same day cancellations.");
  chart("sur", { tooltip: { trigger: "axis", valueFormatter: v => fmtPct(v) }, legend: { top: 0, right: 0, type: "scroll" }, grid: { left: 48, right: 16, top: 34, bottom: 36 }, xAxis: { type: "category", data: grid, name: "days since booking", nameLocation: "middle", nameGap: 24 }, yAxis: { type: "value", min: 0.4, max: 1, name: "share not cancelled", nameLocation: "middle", nameGap: 36, axisLabel: { formatter: v => fmtPct(v) } },
    series: sv.map((r, i) => ({ name: r.market_segment, type: "line", data: grid.map(d => r["s" + d]), lineStyle: { width: r.market_segment === "Online TA" ? 2.5 : 1.5, color: cats[i % cats.length] }, itemStyle: { color: cats[i % cats.length] }, symbol: "circle", symbolSize: 4 })) });
  const out = coh.outcomes, mature = out.filter(r => r.open_share < 0.05), lastM = mature[mature.length - 1];
  setTitle("coh", lastM ? `Bookings made in ${lastM.cohort} realised ${fmtPct(lastM.stayed_share)} of their booked value, ${fmtPct(lastM.canceled_share + lastM.no_show_share)} was cancelled or did not show` : "How each month's bookings turned out", "Booked value of the bookings made in each month, split by what happened to them. A booking made in January for a stay in November is still ahead today, so every month keeps a grey part until all its stays have passed. The title reads the latest month whose bookings are at least 95% resolved.");
  chart("coh", { tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: v => fmtPct(v, 1) }, legend: { top: 0, right: 0 }, grid: { left: 44, right: 12, top: 30, bottom: 40 }, xAxis: { type: "category", data: out.map(r => r.cohort), name: "month the booking was made", nameLocation: "middle", nameGap: 26 }, yAxis: { type: "value", min: 0, max: 1, axisLabel: { formatter: v => fmtPct(v) } },
    series: [{ name: "stayed", type: "bar", stack: "o", data: out.map(r => r.stayed_share), itemStyle: { color: C.blueLight }, barMaxWidth: 26 }, { name: "cancelled", type: "bar", stack: "o", data: out.map(r => r.canceled_share), itemStyle: { color: C.red } }, { name: "no show", type: "bar", stack: "o", data: out.map(r => r.no_show_share), itemStyle: { color: reds[0] } }, { name: "stay still ahead", type: "bar", stack: "o", data: out.map(r => r.open_share), itemStyle: { color: C.grayLight } }] });
}

// guests
async function renderGuests() {
  const page = document.getElementById("page");
  page.innerHTML = head("Guests", "Who the guests are: how they travel, whether they came before, what their history and engagement say about the risk of cancelling, how far ahead each group books and how long it stays, and which meal plans it takes. Everything is computed on the last 12 months of the stream and refreshes with it.") + `
  <section class="panel"><div class="bans row" id="g-bans"></div><p class="src">Source: ${sources.bookings}, last 12 months against the 12 months before, outcomes known today</p></section>
  <div class="grid" style="margin-top:22px">
    ${panel(6, "per", "Who books", "", `<div style="overflow:auto"><table id="per" class="mini"></table></div>`, "", "Source: " + sources.bookings + ", bookings made in the last 12 months with a known outcome")}
    ${panel(6, "eng", "Engagement", "", `<div style="overflow:auto"><table id="eng" class="mini"></table></div>`, "", "Source: " + sources.bookings + ", bookings made in the last 12 months with a known outcome")}
    ${panel(6, "stay", "How each group books and stays", "", `<div style="overflow:auto"><table id="stay" class="mini"></table></div>`, "", "Source: " + sources.bookings + ", bookings made in the last 12 months with a known outcome")}
    ${panel(6, "meal", "Meal plans", "", `<div style="overflow:auto"><table id="meal" class="mini"></table></div>`, "", "Source: " + sources.bookings + ", bookings made in the last 12 months with a known outcome")}
  </div>`;
  const [k, per, his, eng] = await Promise.all([api("guests/kpis"), api("guests/personas"), api("guests/history"), api("guests/engagement")]);
  const cur = k.current, ly = k.last_year, byH = k.cancel_rate_by_history, byR = k.cancel_rate_by_requests;
  const lowerGood = (a, b) => a == null || b == null ? "" : `<span class="d ${a <= b ? "good" : "bad"}">${a <= b ? "▼" : "▲"} ${signed(a - b, 1)} vs 12 months before</span>`;
  const higherGood = (a, b) => a == null || b == null ? "" : `<span class="d ${a >= b ? "good" : "bad"}">${a >= b ? "▲" : "▼"} ${signed(a - b, 1)} vs 12 months before</span>`;
  const deltaFine = (a, b) => { const d = a / b - 1; return `<span class="d ${d >= 0 ? "good" : "bad"}">${d >= 0 ? "▲" : "▼"} ${signed(d, 1)} vs the 12 months before</span>`; };
  const fc = eng.family_cancel || {};
  fill("g-bans", ban("Families with children", fmtPct(cur.family_share, 1), `${higherGood(cur.family_share, ly.family_share)}; they cancel ${fmtPct(fc["with children"])} against ${fmtPct(fc["adults only"])} for adults only`)
    + ban("Repeat guests", fmtPct(cur.repeat_share, 1), `${higherGood(cur.repeat_share, ly.repeat_share)}; they cancel ${fmtPct(byH["repeat guest"])} against ${fmtPct(byH["first time"])} for first time guests`)
    + ban("Guests who cancelled before", fmtPct(cur.prior_cancel_share, 1), `${lowerGood(cur.prior_cancel_share, ly.prior_cancel_share)}; they cancel ${fmtPct(byH["prior cancellation"])} of the time against ${fmtPct(k.cancel_rate_no_prior)} for guests with no cancellation on record`, "red")
    + ban("Bookings with no special request", fmtPct(cur.no_request_share, 1), `${lowerGood(cur.no_request_share, ly.no_request_share)}; they cancel ${fmtPct(byR["none"])} against ${fmtPct(byR["one or more"])} with a request`, "red"));
  const miniTable = (id, rows, labelKey, cols) => {
    const scale = Object.fromEntries(cols.map(c => { const v = rows.map(r => r[c.key]).filter(x => x != null); return [c.key, [Math.min(...v), Math.max(...v)]]; }));
    const shade = (c, v) => { const [lo, hi] = scale[c.key]; const f = c.color === "red" ? redFill : blueFill; return `background:${f(v, lo, hi)};color:${fillText(v, lo, hi)}`; };
    let out = `<thead><tr><th>Hotel</th><th>${cols[0].group}</th>${cols.map(c => `<th class="num">${c.label}</th>`).join("")}</tr></thead><tbody>`;
    for (const h of hotels) for (const r of rows.filter(x => x.hotel === h).sort((a, b) => b[cols[0].key] - a[cols[0].key])) out += `<tr><td>${h.split(" ")[0]}</td><td>${r[labelKey]}</td>${cols.map(c => r[c.key] == null ? `<td class="num">few cases</td>` : `<td class="num fill" style="${shade(c, r[c.key])}">${c.fmt(r[c.key])}</td>`).join("")}</tr>`;
    fill(id, out + "</tbody>");
  };
  miniTable("per", per.filter(r => r.persona !== "other"), "persona", [{ key: "share", label: "Bookings", group: "Party", color: "blue", fmt: v => fmtPct(v) }, { key: "cancel_rate", label: "Cancel rate", color: "red", fmt: v => fmtPct(v) }, { key: "adr_per_guest", label: "Rate per guest", color: "blue", fmt: v => fmtEur.format(v) }]);
  miniTable("stay", per.filter(r => r.persona !== "other"), "persona", [{ key: "share", label: "Bookings", group: "Party", color: "blue", fmt: v => fmtPct(v) }, { key: "avg_lead_time", label: "Days ahead", color: "blue", fmt: v => Math.round(v) }, { key: "avg_nights", label: "Nights", color: "blue", fmt: v => v.toFixed(1) }, { key: "special_requests", label: "Requests", color: "blue", fmt: v => v.toFixed(2) }]);
  miniTable("meal", eng.meal.filter(r => r.meal !== "Undefined"), "meal", [{ key: "share", label: "Bookings", group: "Meal plan", color: "blue", fmt: v => fmtPct(v) }, { key: "cancel_rate", label: "Cancel rate", color: "red", fmt: v => fmtPct(v) }, { key: "avg_adr", label: "Rate", color: "blue", fmt: v => fmtEur.format(v) }]);
  miniTable("eng", eng.requests.map(r => ({ ...r, label: r.requests === "0" ? "no request" : r.requests === "1" ? "1 request" : r.requests + " requests" })), "label", [{ key: "share", label: "Bookings", group: "Requests", color: "blue", fmt: v => fmtPct(v) }, { key: "cancel_rate", label: "Cancel rate", color: "red", fmt: v => fmtPct(v) }, { key: "avg_adr", label: "Rate", color: "blue", fmt: v => fmtEur.format(v) }]);
  const topPer = per.filter(r => r.hotel === "City Hotel").sort((a, b) => b.share - a.share)[0], fam = per.find(r => r.hotel === "City Hotel" && r.persona === "family with children");
  const topPay = per.filter(r => r.hotel === "City Hotel" && r.persona !== "other").sort((a, b) => b.avg_adr - a.avg_adr)[0];
  if (topPer) setTitle("per", `${topPer.persona.charAt(0).toUpperCase() + topPer.persona.slice(1)}s are ${fmtPct(topPer.share)} of City Hotel bookings${topPay ? `; ${topPay.persona === topPer.persona ? "they" : topPay.persona.replace("family", "families")} pay the most per night, ${fmtEur.format(topPay.avg_adr)}` : ""}`, "Share of bookings by party composition, the cancel rate and the rate per guest of each group. Blue deepens with share and rate, red with cancellations.");
  setTitle("eng", `Bookings without a special request cancel ${fmtPct(byR["none"])} of the time, with one or more ${fmtPct(byR["one or more"])}`, "Cancel rate by number of special requests made at booking, with the share of bookings and the average rate of each group.");
  const longest = per.filter(r => r.persona !== "other").sort((a, b) => b.avg_nights - a.avg_nights)[0], earliest = per.filter(r => r.persona !== "other").sort((a, b) => b.avg_lead_time - a.avg_lead_time)[0];
  if (longest && earliest) setTitle("stay", `${longest.persona.charAt(0).toUpperCase() + longest.persona.slice(1)}s stay longest at the ${longest.hotel}, ${longest.avg_nights.toFixed(1)} nights; ${earliest.persona}s book furthest ahead, ${Math.round(earliest.avg_lead_time)} days`, "Average lead time, length of stay and special requests per party composition and hotel.");
  const hb = eng.meal.filter(r => r.meal === "HB").sort((a, b) => b.share - a.share)[0], bb = eng.meal.find(r => r.hotel === (hb ? hb.hotel : "City Hotel") && r.meal === "BB");
  if (hb && bb) setTitle("meal", `Half board is ${fmtPct(hb.share)} of ${hb.hotel} bookings and pays ${fmtEur.format(hb.avg_adr)} against ${fmtEur.format(bb.avg_adr)} for bed and breakfast`, "Share of bookings, cancel rate and average rate per meal plan and hotel.");
}

const pages = { brief: renderBrief, recommendations: renderRecommendations, forecast: renderForecast, risk: renderRisk, demand: renderDemand, market: renderMarket, agents: renderAgents, partners: renderAgents, guests: renderGuests };
async function route() {
  const name = (location.hash || "#brief").slice(1), fn = pages[name] || renderBrief, token = ++routeSeq;
  document.querySelectorAll(".nav a").forEach(a => a.classList.toggle("active", a.dataset.page === name));
  clearCharts();
  try { await fn(); if (token !== routeSeq) return; setTimeout(() => charts.forEach(c => c.resize()), 50); }
  catch (e) { if (token !== routeSeq) return; if (!(e instanceof TypeError && /null/.test(e.message))) fill("page", `<h1 style="text-align:left">Something did not load</h1><p class="lede">${e.message}. Reload in a few seconds.</p>`); }
  if (token !== routeSeq) return;
  document.getElementById("page")?.insertAdjacentHTML("beforeend", `<p class="stamp">Rendered ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC. Booking stream and feeds are live.</p>`);
}
async function renderHeader() {
  const h = await api("health");
  document.getElementById("clock-date").textContent = new Date().toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  document.getElementById("updated").textContent = "Scores refresh every 10 minutes, books nightly";
}
async function main() { echarts.registerTheme("light-cockpit", theme); window.addEventListener("hashchange", route); await renderHeader(); await route(); }
main();
