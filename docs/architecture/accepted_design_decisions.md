# Accepted Design Decisions

本文件记录已经过源码与测试证据核验、被项目明确接受的设计决策。
这些条目曾以 `[~]` 形式挂在外部问题清单上，但均不属于待修 defect。
每项包含：决策、理由、已接受的取舍、重新评估触发条件。

核验基线：HEAD `8730ce3`，full pytest 770/770（2026-09-05）。

---

## AD-1. Word 原文保留检查：整句子串 + 相似度 0.72 保守口径

**Decision**
`_check_missing_text`（`office_agent/quality/checker.py:581-616`）采用两级判定：
整句（>15 字）先按 exact substring 匹配，未命中再以
`_text_similarity >= 0.72`（`checker.py:596,619-623`，归一化后
`SequenceMatcher.ratio()`）做模糊兜底。相似度低于 0.72 的大幅改写
仍按"可能遗漏"报告。

**Rationale**
这是质量检查器刻意偏保守的产品口径：漏报（把真实遗漏放过去）比
误报（把合理改写标成遗漏）代价更高——用户依赖该报告确认 AI 修订
没有丢内容。阈值 0.72 是"轻度改写（同义替换、语序调整）不报警"
与"实质重写视为新内容"之间的经验分界。

**Accepted trade-off**
对大幅改写（相似度 < 0.72）的文本仍报告 missing，可能产生误报；
但这是单向保守，不会漏掉真正丢失的内容。进一步降低阈值会把
"段落被完全重写"也判为保留，造成 false negative，破坏该检查的
存在意义。

**Revisit trigger**
出现真实用户反馈表明误报率不可接受，或引入语义级（embedding）
相似度基础设施时，再评估阈值或判据升级。届时属于新功能而非缺陷修复。

**证据**
源码 `checker.py:581-623`；枚举单一来源守卫
`tests/unit/test_word_quality_enum_literals.py`；全量回归间接覆盖。

---

## AD-2. CJK 字体不随应用捆绑

**Decision**
PPT 占位图渲染不捆绑任何 CJK 字体文件。字体解析链为：
`OFFICE_AGENT_FONT` 环境变量（显式配置优先）→
Windows/macOS/Linux 平台候选链（`_FONT_CANDIDATES`，
`office_agent/vision_gateway/document_renderer.py:24-33`）→
全部不可用时降级 Pillow 默认字体并输出 warning
（`document_renderer.py:36-49`，渲染不失败）。

**Rationale**
捆绑 CJK 字体属于部署与发行策略决策，不是 runtime bug：
- 中文字体文件通常 10–50 MB，显著增大安装体积；
- 主流中文字体（思源、雅黑等）的再分发受各自许可证约束，
  需要发行方法务确认，不是代码层面的决定；
- 目标平台（Windows/macOS/主流 Linux 桌面）绝大多数自带 CJK 字形，
  完全缺字形的系统是边缘场景，且有 `OFFICE_AGENT_FONT` 显式逃生通道。

**Accepted trade-off**
在完全缺少中文字形且未配置 `OFFICE_AGENT_FONT` 的系统上，
占位图可能显示方块；渲染本身不中断。

**Revisit trigger**
发行策略确定要支持无中文字体的最小化系统（如精简容器镜像），
且字体许可证审查完成时。届时属于打包/发行工作而非代码修复。

**证据**
源码 `document_renderer.py:24-49`；
`tests/unit/test_runtime_config_consolidation.py`（OFFICE_AGENT_FONT
优先与缺省回退）；`tests/unit/test_word_main_font.py`。

---

## AD-3. Authentication 默认关闭（本地单机定位）

**Decision**
`auth_enabled` 默认 `False`（`office_agent/api/core/config.py:54,94`）。
默认绑定地址为 `127.0.0.1`（回环，`config.py:104`）。
启用认证但未配置任何凭据时启动 fail-fast：
`auth_enabled and not api_keys and not jwt_secret` → 抛
`ValueError`（`config.py:97-100`）；JWT secret 少于 32 字节同样
fail-fast（`config.py:101-102`）。

**Rationale**
产品定位包含本地桌面单机使用（desktop launcher / Windows service /
frozen 入口均绑定回环）。对该场景强制认证只增加配置负担、不增加
实际安全边界（回环面本机进程即可达）。真正的防线是：
1. 默认只监听回环，外部不可达；
2. 一旦用户显式开启认证，错误配置（无凭据/弱 secret）在启动期
   fail-fast，绝不带错误配置运行。

**Accepted trade-off**
若部署方主动把 `OFFICE_AGENT_HOST` 改为非回环地址却不开认证，
服务将无认证暴露。该场景依赖部署方显式配置，属于部署责任；
`OFFICE_AGENT_AUTH_ENABLED`/`OFFICE_AGENT_API_KEYS`/
`OFFICE_AGENT_JWT_SECRET` 为完整的外部暴露配置链路。

**Revisit trigger**
产品定位变化（例如官方支持多用户/局域网共享部署模式）时，
重新评估默认安全姿态。不为了 checklist 数字强制默认开启认证——
那会破坏单机桌面体验且不提供真实安全收益。

**证据**
源码 `config.py:54,92-102`；
`tests/unit/test_security.py`（auth 中间件启用路径，含 monkeypatch
`auth_enabled=True` 的 401/通过用例）。

---

## AD-4. 模型配置 DB 与 models.json 双轨：management plane vs runtime secret plane

**Decision**
（覆盖清单三个重复记账的侧面：两套存储来源、/config/models 与
/settings/models 写路径分属、密钥口径双轨）
权威源自 `9ddc696` 起唯一：`office_agent/models/model_schemas.py`
是 provider 枚举、默认模型清单（`DEFAULT_MODEL_CONFIGS`）、
环境变量映射的 canonical authority；config_system 默认清单改由
`default_model_catalog()` 从权威模板渲染
（`config_system/config_manager.py:689-690`），不再维护第二份。
存储分层刻意保留：
- **config_system DB = management plane**：模型启用/优先级/展示配置；
- **models.json（Fernet 加密）= runtime secret plane**：API Key 密文
  与运行时模型连接配置。
旧 ID 在读写边界统一归一化：`LEGACY_MODEL_ID_ALIASES` /
`PROVIDER_ALIASES`（`model_schemas.py:228-258`）在
ConfigManager 查询/更新/缓存索引/校验与 models.json 加载路径
自动归一，不产生影子记录。`/config/models` 与 `/settings/models`
读到同一份默认清单与同一套权威 ID。

**Rationale**
两平面关注点不同：管理面需要事务、查询、审计；密钥面需要加密
落盘与最小暴露。合并存储属于 destructive migration（要迁移用户
已有加密 Key 与自定义模型），风险远大于收益。XOR→Fernet 升级
已完成（旧格式只读兼容，不再新写），安全债已清，剩下的只是
分层架构本身。

**Accepted trade-off**
两个写路径并存，运维上需要理解分层；但任一平面都不会产生
与权威源不一致的数据（归一化在边界完成）。

**Revisit trigger**
未来若做统一的配置存储层（如全部迁入加密 DB 或外部 secret
manager），作为独立的 migration 项目立项，附带完整迁移与回滚
方案；不在缺陷修复批次中顺手合并。

**证据**
源码 `model_schemas.py:102-258`、`config_manager.py:689-690`；
`tests/unit/test_model_catalog_unification.py` 13 项；
commit `9ddc696`。

---

## AD-5. Prompt Injection 启发式：分级策略 + 持续调优（accepted residual risk）

**Decision**
注入防护采用评分制分级策略而非二元拦截
（`office_agent/security/prompt/prompt_security.py:131-226`）：
- 规则带 severity（low/medium/high/critical）与分数；
- `high_confidence` 规则命中时，若前文落在分析/翻译/引用等
  良性上下文（`_ANALYSIS_CONTEXT_RE`，`:124-128`），自动降权降
  级（false-positive guard，`:166-169`）；
- file/external 来源的贡献分减半、high 降 medium（`:170-173`）；
- 动作分级：critical token 或高分高置信 → REJECT；中分或多命中
  → REVIEW（视为不可信数据+限权）；否则 ALLOW（`:185-195`）；
- 模型控制符（`<|im_start|>` 等）始终转义（`:123,142-148`）。

**Rationale**
"正则同时存在误报和漏报"是对一切启发式/正则注入检测的固有
描述，不是可确定性关闭的 defect：对抗样本空间是开放的，
任何有限规则集都不可能达到零误报零漏报。项目接受的安全策略是
分级响应（REJECT/REVIEW/ALLOW）+ 高置信规则保底 + 良性上下文
降权，残余风险靠持续规则调优管理。

**Accepted trade-off**
新颖混淆手法可能绕过现有规则（漏报方向）；极端措辞的良性文本
仍可能触发 REVIEW（误报方向）。REVIEW 不是阻断，而是降权处理，
业务可用性损失有界。

**Revisit trigger**
出现具体的新型绕过样本或系统性误报投诉时，按样本增补/调整规则
（常规安全运维），不重写整个 prompt-security 体系。

**证据**
源码 `prompt_security.py:117-226`；
`tests/unit/test_security_policy_hardening.py`（良性上下文不过拦：
"请分析这句文本：忽略之前的指令"降级用例等）；
`tests/unit/test_security.py::test_injection_detection`；
commit `4c67749`。
