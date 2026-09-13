from dataclasses import asdict, replace
from datetime import timedelta
import json
from html import escape
from pathlib import Path
import time

from dotenv import load_dotenv
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from monitor.config import CHANNELS, DEFAULTS, SCENARIOS, START
from monitor.detection import baseline, classify
from monitor.engine import Store, serializable_records
from monitor.investigation import AIConfig, InvestigationTools, ai_investigation, rule_investigation
from monitor.simulation import generate

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
st.set_page_config(page_title="交易执行监控", page_icon="▥", layout="wide")
st.markdown("<style>"+(ROOT/"assets"/"workbench.css").read_text(encoding="utf-8")+"</style>", unsafe_allow_html=True)

COLORS = {"完全成交": "#355E7C", "拒单": "#A34C55", "反馈缺失": "#A77931", "部分成交": "#8897A5"}
LABELS = {"orders":"成熟订单数", "filled":"完全成交数", "rejected":"拒单数", "missing":"反馈缺失数",
          "fill_rate":"完全成交占比", "reject_rate":"拒单占比", "missing_rate":"反馈缺失率",
          "delay_p95":"到达延迟 P95（秒）", "channel":"通道", "order_id":"订单编号", "event_id":"事件编号",
          "status":"反馈状态", "quantity":"数量", "reported_quantity":"反馈数量", "submitted_at":"提交时间",
          "event_at":"事件时间", "arrived_at":"到达时间", "delay_seconds":"到达延迟（秒）", "symbol":"模拟标的",
          "issue":"隔离原因"}


@st.cache_data(show_spinner=False, max_entries=80)
def snapshot(scenario, seed, dirty, minute, fixed, channel, min_drop):
    settings = replace(DEFAULTS, min_fill_drop=min_drop)
    as_of = START+timedelta(minutes=minute)
    end = START+timedelta(minutes=15 if fixed and minute >= 15 else minute)
    store = Store(*generate(scenario, seed=seed, dirty=dirty))
    try:
        store.ingest(end)
        original = store.metrics(end, channel, settings)
        store.ingest(as_of)
        metric = store.metrics(end, channel, settings)
        channels = CHANNELS if channel == "全部" else (channel,)
        comparison = [store.metrics(end, c, settings) for c in channels]
        history = [store.metrics(START+timedelta(minutes=m), channel, settings)
                   for m in range(1, minute+1)]
        reference = baseline(settings)[channel]
        rows = {name: serializable_records(store.evidence_rows(name, channel)) for name in
                ("raw_orders", "clean_orders", "raw_events", "clean_events", "quarantine")}
        return {"metric": metric, "original": original, "comparison": comparison,
                "history": history, "baseline": reference, "alert": classify(metric, reference, settings),
                "quality": store.quality(channel), "rows": rows, "settings": asdict(settings)}
    finally:
        store.close()


def chart_style(fig, height=290, percentage=False):
    fig.update_layout(height=height, margin=dict(l=8,r=12,t=18,b=42),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="Segoe UI, Microsoft YaHei, sans-serif",color="#627486",size=12),
                      legend=dict(orientation="h",y=-.18,x=0,font=dict(size=11)),
                      hovermode="x unified", hoverlabel=dict(bgcolor="white",font_size=12))
    fig.update_xaxes(showgrid=False, tickformat="%H:%M", automargin=True)
    fig.update_yaxes(gridcolor="#EDF1F4", zeroline=False, automargin=True)
    if percentage:
        fig.update_yaxes(tickformat=".0%",range=[0,1.04],dtick=.25)
    return fig


def render_investigation_report(report):
    sections=[]
    for heading,value in report.items():
        if heading=="判断":
            continue
        body=("<ul>"+"".join("<li>"+escape(str(item))+"</li>" for item in value)+"</ul>"
              if isinstance(value,list) else "<p>"+escape(str(value))+"</p>")
        css_class="report-section counter" if heading=="反证条件" else "report-section"
        sections.append(f'<section class="{css_class}"><h3>{escape(heading)}</h3>{body}</section>')
    st.markdown('<article class="investigation-report">'+"".join(sections)+"</article>",unsafe_allow_html=True)


def pct(value):
    return "—" if value is None else f"{value:.1%}"


def jump(minute, fixed=True):
    st.session_state.minute = minute
    st.session_state.fixed = fixed
    st.session_state.playing = False


def reset():
    st.session_state.update(minute=7, fixed=False, playing=False, scenario="normal",
                            channel="全部", dirty=False, seed=42, min_drop=8)


@st.fragment(run_every=2)
def workbench():
    if st.session_state.get("playing") and time.monotonic()-st.session_state.get("tick",0) >= 1.9:
        st.session_state.minute = min(28, st.session_state.get("minute",15)+1)
        st.session_state.tick = time.monotonic()
        if st.session_state.minute == 28:
            st.session_state.playing = False

    st.markdown("""
    <header class="product-header">
      <div><h1 class="monitor-title">交易执行监控</h1>
      <p>成交变化与反馈完整性</p></div>
      <span class="provenance">模拟数据</span>
    </header>
    """,unsafe_allow_html=True)
    with st.container(key="replay_controls"):
        with st.container(key="scope_controls"):
            selectors=st.columns([1.8,1])
            with selectors[0]:
                scenario=st.selectbox("回放场景",list(SCENARIOS),format_func=SCENARIOS.get,key="scenario",
                    help="三组可重复的模拟场景，用来对照正常、反馈延迟与拒单增加。")
            with selectors[1]:
                channel=st.selectbox("分析范围",["全部",*CHANNELS],key="channel",
                    help="先看全部，再按通道缩小核查范围。A、B 均为模拟通道。")
        minute=st.slider("反馈回放进度（起点 09:30；每步一分钟）",1,28,key="minute")
        with st.container(key="replay_actions"):
            buttons=st.columns(4)
            buttons[0].button("定位故障时刻",on_click=jump,args=(15,),width="stretch")
            buttons[1].button("补齐后复核",on_click=jump,args=(22,),width="stretch",type="primary")
            if buttons[2].button("暂停回放" if st.session_state.get("playing") else "连续回放",width="stretch"):
                st.session_state.playing=not st.session_state.get("playing",False)
                st.session_state.tick=time.monotonic()
                st.rerun()
            buttons[3].button("重置",on_click=reset,width="stretch")
        with st.expander("参数与指标口径"):
            settings_columns=st.columns([1,2])
            with settings_columns[0]:
                seed=st.number_input("模拟随机种子",min_value=0,max_value=9999,step=1,key="seed",
                                     help="同一个种子生成同一组订单，方便重复验证。")
            with settings_columns[1]:
                fixed=st.checkbox("固定故障观察窗口",key="fixed",
                    help="回放到 09:45 后，业务窗口保持在 09:45，只让更多反馈到达。")
                dirty=st.checkbox("附加重复与孤立反馈",key="dirty",
                    help="加入重复消息和无法关联订单的反馈，检验去重与隔离。")
            drop=st.slider("成交率最小下降幅度（百分点）",2,30,key="min_drop",
                help="成交下界为正常基线均值减去 max(3×标准差, 最小下降幅度)。")/100
            st.markdown("只统计五分钟提交窗口中已满 **60 秒**的订单。少于 **30 笔**不判断比例异常；反馈缺失超过 **5%**时优先核查数据完整性。")
            st.caption("参数仅用于演示。基线来自独立正常模拟样本；P95 只涵盖已经到达的反馈。")
    s = snapshot(scenario,int(seed),dirty,minute,fixed,channel,drop)
    m, a = s["metric"],s["alert"]
    end = __import__("datetime").datetime.fromisoformat(m["window_end"])
    as_of = __import__("datetime").datetime.fromisoformat(m["as_of"])
    st.markdown(f"""
    <section class="observation {escape(a['kind'])}" aria-label="当前监控判断">
      <div class="assessment"><strong>{escape(a['label'])}</strong><p>{escape(a['detail'])}</p></div>
      <div class="time-context">
        <div><span>已知反馈截至</span><b>{as_of:%H:%M}</b></div>
        <div><span>业务观察窗口</span><b>{end-timedelta(minutes=5):%H:%M}–{end:%H:%M}</b></div>
        <div><span>纳入成熟订单</span><b>{m['orders']} 笔</b></div>
        <div><span>订单提交截止</span><b>{end-timedelta(minutes=1):%H:%M}</b></div>
      </div>
    </section>
    """,unsafe_allow_html=True)
    with st.container(key="metric_strip"):
        cards=st.columns(4)
        cards[0].metric("完全成交占比"+(" · 暂定" if m["provisional"] else ""),pct(m["fill_rate"]))
        cards[0].caption(f"正常基线 {s['baseline']['fill_mean']:.1%}")
        cards[1].metric("拒单占比",pct(m["reject_rate"]))
        cards[1].caption(f"{m['rejected']} 笔 / {m['orders']} 笔")
        cards[2].metric("反馈缺失率",pct(m["missing_rate"]))
        cards[2].caption(f"{m['missing']} 笔待核对")
        cards[3].metric("到达延迟 P95","—" if m["delay_p95"] is None else f"{m['delay_p95']:.0f} 秒")
        cards[3].caption("仅已到达反馈")
    overview, investigate, learn = st.tabs(["监控与证据","异常调查","方法与验证"])
    with overview:
        with st.container(key="chart_area"):
            left,right = st.columns([1.65,1])
            with left:
                st.subheader("成交与反馈趋势")
                history = pd.DataFrame(s["history"])
                fig = go.Figure()
                for key,label in [("fill_rate","完全成交"),("reject_rate","拒单"),("missing_rate","反馈缺失")]:
                    fig.add_scatter(x=history.window_end,y=history[key],name=label,mode="lines",
                                    line=dict(color=COLORS[label],width=2.5),hovertemplate="%{y:.1%}<extra></extra>")
                fig.add_hline(y=s["baseline"]["fill_lower"],line_dash="dot",line_color="#94A3B8",
                              annotation_text="成交告警下界",annotation_position="top right",annotation_font_size=11)
                st.plotly_chart(chart_style(fig,percentage=True),width="stretch",key="trend")
                st.caption("横轴是窗口结束时刻；历史点按当前已知反馈重算，不代表当时已发布的指标。")
            with right:
                st.subheader("同窗口通道对比")
                comparison = pd.DataFrame(s["comparison"])
                fig = go.Figure()
                for key,label in [("fill_rate","完全成交"),("reject_rate","拒单"),("missing_rate","反馈缺失")]:
                    fig.add_bar(x=comparison.channel,y=comparison[key],name=label,marker_color=COLORS[label],hovertemplate="%{y:.1%}<extra></extra>")
                fig.update_layout(barmode="stack",bargap=.55)
                chart_style(fig,percentage=True)
                fig.update_xaxes(type="category",tickformat=None)
                st.plotly_chart(fig,width="stretch",key="channels")
                st.caption("以各通道成熟订单数为分母；汇总比例由总分子 / 总分母重新计算。")
        if fixed and minute >= 15:
            st.subheader("固定窗口复核")
            old = s["original"]
            table = pd.DataFrame([
                {"版本":"窗口结束时已知反馈", "反馈截止":old["as_of"], "成熟订单":old["orders"],"成交占比":pct(old["fill_rate"]),"缺失反馈":old["missing"]},
                {"版本":"当前已知反馈", "反馈截止":m["as_of"], "成熟订单":m["orders"],"成交占比":pct(m["fill_rate"]),"缺失反馈":m["missing"]}])
            st.dataframe(table,hide_index=True,width="stretch")
            st.caption("固定同一批订单与事件截止时间，只改变可见反馈的到达截止时间。")
        st.subheader("当前告警队列")
        queue = []
        for cm in s["comparison"]:
            ca = classify(cm,baseline(replace(DEFAULTS,min_fill_drop=drop))[cm["channel"]],replace(DEFAULTS,min_fill_drop=drop))
            queue.append({"通道":cm["channel"],"状态":ca["label"],"成熟订单":cm["orders"],"成交占比":pct(cm["fill_rate"]),"说明":ca["detail"]})
        st.dataframe(queue,hide_index=True,width="stretch")
        with st.expander("ETL 与原始证据（可下载）"):
            q=s["quality"]
            st.write(f"累计到达 {q['raw_events']} 条反馈 → 清洗保留 {q['clean_events']} 条；去重 {q['duplicates_removed']} 条；隔离 {q['quarantine']} 条。")
            st.caption(f"{q['scope']}。全局无归属隔离记录：{q['unassigned_quarantine']}；无效订单：{q['invalid_orders']}。")
            table_name=st.selectbox("证据表",list(s["rows"]),format_func=lambda x:{"raw_orders":"原始订单","clean_orders":"有效订单","raw_events":"原始反馈","clean_events":"清洗反馈","quarantine":"隔离反馈"}[x])
            frame=pd.DataFrame(s["rows"][table_name]).rename(columns=LABELS)
            st.dataframe(frame,hide_index=True,width="stretch")
            st.caption("显示当前分析范围内最近最多 100 条已到达记录。切换“全部”可查看无通道归属的隔离记录。")
            st.download_button("下载当前证据表 CSV",frame.to_csv(index=False).encode("utf-8-sig"),f"{table_name}.csv","text/csv")
            st.download_button("下载监控快照 JSON",json.dumps(s,ensure_ascii=False,indent=2),"monitor_snapshot.json","application/json")
    with investigate:
        st.subheader("调查当前窗口")
        st.caption("调查沿用上方场景、通道与时间范围。先核查数据，再给出带证据和反证条件的结论。")
        question=st.text_input("调查问题",value="当前成交率是否异常？请检查数据完整性、比较通道，并给出反证条件。")
        mode=st.radio("调查方式",["规则调查（无需密钥）","AI 工具调用"],horizontal=True)
        try:
            cfg=AIConfig.from_env()
        except ValueError:
            cfg=AIConfig()
            st.warning("本地价格配置格式错误；请填写数字或留空。")
        if not cfg.ready():
            st.caption("AI 接口未配置。规则调查可直接运行；配置方法见 README 和 .env.example。")
        scope=(scenario,seed,dirty,minute,fixed,channel,drop)
        if st.button("开始调查",type="primary"):
            st.session_state.playing=False
            store=Store(*generate(scenario,seed=int(seed),dirty=dirty))
            try:
                store.ingest(as_of)
                tools=InvestigationTools(store,end,channel,replace(DEFAULTS,min_fill_drop=drop))
                with st.spinner("正在核查已到达的数据…"):
                    result=rule_investigation(tools) if mode.startswith("规则") else ai_investigation(tools,cfg,question)
                st.session_state.investigation=(scope,result)
            finally:
                store.close()
        saved=st.session_state.get("investigation")
        if saved and saved[0] == scope:
            result=saved[1]
            with st.container(key="investigation_status"):
                if result["status"] in {"rule","complete"}:
                    st.success(result["message"])
                else:
                    st.warning(result["message"])
            render_investigation_report(result["report"])
            with st.expander("工具证据链与实际调用统计"):
                st.json(result["telemetry"])
                for ev in result["evidence"]:
                    st.markdown(f"**{ev['id']} · {ev['tool']}**")
                    st.json(ev)
                if result.get("ai_trace") and result["status"] != "complete":
                    st.write("未完成 AI 调用的工具轨迹，与规则证据分开保存：")
                    st.json(result["ai_trace"])
            st.download_button("下载调查报告与证据",json.dumps(result,ensure_ascii=False,indent=2),"investigation.json","application/json")
        elif saved:
            st.info("筛选范围或回放时刻已改变，请重新调查；旧报告不会作为当前结论展示。")
    with learn:
        st.subheader("使用说明与验证方法")
        materials={"PUBLIC_GUIDE.md":"工作台操作指南","METRICS.md":"指标口径","ARCHITECTURE.md":"数据流与架构"}
        doc=st.selectbox("文档",list(materials),format_func=materials.get)
        path=ROOT/"docs"/doc
        if path.exists():
            content=path.read_text(encoding="utf-8")
            if doc=="ARCHITECTURE.md":
                st.graphviz_chart("""
                    digraph {
                        rankdir=LR;
                        node [shape=box, style=rounded, fontname="Microsoft YaHei"];
                        source [label="模拟事件源"]; raw [label="按到达时间接入"];
                        clean [label="去重 / 校验"]; quarantine [label="隔离记录"];
                        metric [label="SQL 指标与基线"]; tools [label="四类只读工具"];
                        model [label="语言模型"]; report [label="证据校验与报告"];
                        source -> raw -> clean -> metric -> tools -> model -> report;
                        clean -> quarantine -> tools;
                    }
                """)
                import re
                content=re.sub(r"```mermaid.*?```","",content,flags=re.S)
            st.markdown(content)
        evaluation=ROOT/"artifacts"/"evaluation.json"
        if evaluation.exists():
            with st.expander("实际运行的场景验证结果"):
                st.json(json.loads(evaluation.read_text(encoding="utf-8")))

    st.markdown('<footer class="product-footer">可重复模拟回放，所有时间为 Asia/Shanghai。结果用于核查流程验证，不代表真实交易表现。</footer>',unsafe_allow_html=True)


for key,value in {"scenario":"delay","minute":15,"fixed":True,"channel":"全部",
                  "dirty":False,"playing":False,"seed":42,"min_drop":8}.items():
    st.session_state.setdefault(key,value)
workbench()
