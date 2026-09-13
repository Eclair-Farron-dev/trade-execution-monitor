# 交易执行监控

基于 Python、DuckDB 和 Streamlit 的模拟订单监控工作台。提供场景回放、数据清洗、指标计算、固定窗口复核与证据调查。

全部业务数据为模拟数据，不代表真实交易表现。默认规则调查无需模型密钥；真实 AI 服务需另行配置，不将规则结果标为模型输出。

## 运行
使用 Python 3.12，安装 requirements.txt 后执行 streamlit run app.py。

## 部署
Streamlit Community Cloud 入口为 app.py，Python 版本选择 3.12。公开访问权限在应用分享设置中配置。

## 文档
- [操作指南](docs/PUBLIC_GUIDE.md)
- [指标口径](docs/METRICS.md)
- [数据流与架构](docs/ARCHITECTURE.md)
