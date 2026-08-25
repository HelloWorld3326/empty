# -*- coding: utf-8 -*-
import html
W=1240; L=60; R=1180; IW=R-L
PAD=16; BOXH=56; TITLEH=30; ROWGAP=10; GAP=52
AC="#2E9E97"
DEFS=('<defs>'
 '<marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
 '<path d="M0,0 L10,5 L0,10 z" fill="currentColor" fill-opacity="0.55"/></marker>'
 '<marker id="arac" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
 f'<path d="M0,0 L10,5 L0,10 z" fill="{AC}"/></marker>'
 '</defs>')
def esc(t): return html.escape(t)
def rows_for(b,per): return [b[i:i+per] for i in range(0,len(b),per)]

def band(y,title,boxes,per,dashed=False,note=""):
    rws=rows_for(boxes,per)
    h=TITLEH+len(rws)*BOXH+(len(rws)-1)*ROWGAP+PAD
    o=[f'<rect x="{L}" y="{y}" width="{IW}" height="{h}" rx="6" fill="currentColor" fill-opacity="0.035" stroke="currentColor" stroke-opacity="0.22"'+(' stroke-dasharray="6 5"' if dashed else '')+'/>',
       f'<text x="{L+PAD}" y="{y+21}" font-size="12.5" font-weight="600" letter-spacing="1.2" fill="currentColor" fill-opacity="0.62">{esc(title)}</text>']
    if note: o.append(f'<text x="{R-PAD}" y="{y+21}" text-anchor="end" font-size="11.5" fill="currentColor" fill-opacity="0.42">{esc(note)}</text>')
    inner=IW-2*PAD
    for ri,rw in enumerate(rws):
        n=len(rw); g=10; bw=(inner-(n-1)*g)/n; by=y+TITLEH+ri*(BOXH+ROWGAP)
        for ci,b in enumerate(rw):
            lab=b[0]; sub=b[1] if len(b)>1 else ""; kind=b[2] if len(b)>2 else ""
            bx=L+PAD+ci*(bw+g); core=(kind=="core")
            dash=' stroke-dasharray="7 4"' if kind=="p1" else (' stroke-dasharray="2 4"' if kind=="p2" else '')
            fo="0.13" if core else ("0.03" if kind=="p2" else "0.055")
            st=AC if core else "currentColor"; so="0.9" if core else ("0.22" if kind=="p2" else "0.3")
            fill=AC if core else "currentColor"
            o.append(f'<rect x="{bx:.1f}" y="{by}" width="{bw:.1f}" height="{BOXH}" rx="4" fill="{fill}" fill-opacity="{fo}" stroke="{st}" stroke-opacity="{so}"{dash}/>')
            tc=AC if core else "currentColor"; to="1" if core else ("0.62" if kind=="p2" else "0.92")
            o.append(f'<text x="{bx+bw/2:.1f}" y="{by+(24 if sub else 33)}" text-anchor="middle" font-size="13.5" font-weight="600" fill="{tc}" fill-opacity="{to}">{esc(lab)}</text>')
            if sub: o.append(f'<text x="{bx+bw/2:.1f}" y="{by+42}" text-anchor="middle" font-size="11" fill="currentColor" fill-opacity="{0.35 if kind=="p2" else 0.5}">{esc(sub)}</text>')
    return "\n".join(o),h

def darrow(x,y0,y1,label):
    o=[f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y1-9}" stroke="currentColor" stroke-opacity="0.5" stroke-width="1.6" marker-end="url(#ar)"/>']
    if label: o.append(f'<text x="{x+12}" y="{(y0+y1)/2+4}" font-size="11.5" fill="currentColor" fill-opacity="0.62">{esc(label)}</text>')
    return "\n".join(o)

def build(bands,gaps,cross=None,uplabel=None,aria=""):
    y=40; parts=[DEFS]; tops=[]
    for i,(t,note,bx,per) in enumerate(bands):
        s,h=band(y,t,bx,per,False,note); parts.append(s); tops.append((y,h))
        if i<len(bands)-1: parts.append(darrow(300,y+h,y+h+GAP,gaps[i]))
        y+=h+GAP
    if cross:
        s,h=band(y,cross[0],cross[2],cross[3],True,cross[1]); parts.append(s)
        parts.append(f'<line x1="{L-18}" y1="{tops[1][0]}" x2="{L-18}" y2="{y+h/2}" stroke="currentColor" stroke-opacity="0.3" stroke-width="1.4" stroke-dasharray="5 5"/>')
        parts.append(f'<line x1="{L-18}" y1="{y+h/2}" x2="{L-3}" y2="{y+h/2}" stroke="currentColor" stroke-opacity="0.3" stroke-width="1.4" stroke-dasharray="5 5"/>')
        y+=h
    else:
        y-=GAP; y=tops[-1][0]+tops[-1][1]
    if uplabel:
        top=tops[0][0]+tops[0][1]; bot=tops[-1][0]
        parts.append(f'<line x1="{R+18}" y1="{bot}" x2="{R+18}" y2="{top+9}" stroke="{AC}" stroke-opacity="0.85" stroke-width="1.8" marker-end="url(#arac)"/>')
        mid=(top+bot)/2
        parts.append(f'<text x="{R+34}" y="{mid}" font-size="11.5" fill="{AC}" transform="rotate(90 {R+34} {mid})" text-anchor="middle">{esc(uplabel)}</text>')
    H=y+40
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(aria)}" xmlns="http://www.w3.org/2000/svg">\n'+"\n".join(parts)+'\n</svg>'

# ============ FIG 1 技术架构 ============
b1=[("接入层","",[("Web 工作台","会话 · 产物 · 资产 · 管理台"),("IM 渠道","飞书 / 企微 / 钉钉"),("开放 API / SDK","供内部系统集成")],3),
("网关层","唯一对外入口，内部端口不发布",[("API 网关","路由 · 鉴权 · 限流 · CSRF"),("SSE 出口","长连接 · 心跳 · 断点续传")],2),
("应用层  app/","业务编排，可依赖框架层",[("会话服务","thread / message"),("运行编排服务","run 生命周期"),("渠道服务","消息总线 · 去重"),("调度服务","定时与订阅任务"),("资产服务","产物 · 导出"),("审计服务","SQL 与工具留痕"),("认证授权","密码 / SSO · RBAC"),("配额服务","用量与成本")],4),
("框架层  harness/","通用智能体底座，永不反向依赖应用层",[("运行时","RunManager · StreamBridge · 状态对象","core"),("智能体装配","中间件链 · 主智能体","core"),("工具注册表","内置 / 配置化 / 外部协议","core"),("子智能体","独立上下文 · 命名空间流"),("记忆","会话内 + 跨会话"),("模型路由","多 provider · 降级 · 计量"),("护栏","输入输出校验 · 拦截"),("持久化","分域仓储 · 迁移")],4),
("能力层 · 工具与执行","工具集合即产品能力的全集",[("元数据工具","搜表 · 表卡片 · 血缘"),("知识检索工具","向量库 · 口径文档"),("SQL 工具","方言 · 校验 · 执行"),("可视化工具","chart spec"),("沙箱执行","容器隔离 · 资源限额"),("外部工具协议","第三方能力接入")],6),
("数据与基础设施","",[("平台库 PostgreSQL","会话 / 运行 / 资产 / 审计"),("对象存储 S3","产物与导出"),("向量库","知识与样本索引"),("数据源集群","PG · MySQL · StarRocks · Hive"),("模型服务","阿里云百炼"),("沙箱供给 K8s","按需分配执行容器")],6)]
g1=["HTTPS 请求 / SSE 订阅","鉴权通过 → 创建 run","装配上下文 → 启动智能体循环","工具调用（参数校验后下发）","只读 SQL / 向量检索 / 容器执行"]
cross=("横切关注点","贯穿全部层次，不在请求链路上",[("配置中心","模型 / 工具 / 表白名单"),("可观测","链路追踪 · 用量 · 成本"),("安全","脱敏 · 行级权限 · 审批流"),("发布与运维","Helm · CI/CD · 灰度")],4)
open('fig1.svg','w').write(build(b1,g1,cross,"SSE 事件上行：文本增量 + 工具状态 + 有界快照",
 "数据 Agent 平台技术架构：接入层、网关层、应用层、框架层、能力层、数据与基础设施六层自上而下，箭头标注请求下行的每一跳，右侧为 SSE 事件上行链路，底部虚线为横切关注点"))

# ============ FIG 2 产品架构 ============
b2=[("使用者","数据部门为主，逐步开放给业务方",[("数据分析师","取数 · 归因 · 临时分析"),("数据工程师","开发 · 排障"),("业务方","自助问数"),("数据治理岗","资产 · 质量 · 合规")],4),
("接触点","",[("Web 工作台","完整能力"),("IM 助手","轻量问答与推送"),("嵌入式入口","BI / 内部系统")],3),
("产品能力 · 场景","实线 = MVP 覆盖，虚线 = 一期，点线 = 二期",[
 ("自助取数问答","自然语言 → SQL → 结果 → 解读","core"),("元数据问答","表 / 字段 / 口径 / 归属","core"),
 ("分析报告","多轮分析 + 结论 + 图表","p1"),("看板沉淀","把问题固化成看板","p1"),
 ("数据质量","规则建议 · 校验 SQL","p2"),("血缘与影响分析","上下游追溯","p2"),
 ("数据开发助手","ETL 编写与排障","p2"),("主动洞察","异动监测 + 推送","p2")],4),
("平台能力","支撑上层场景的通用机制",[("智能体编排","主 + 子智能体","core"),("工具体系","可插拔扩展","core"),("评测与反馈","黄金集 · 在线评测","core"),
 ("知识与记忆","口径库 · 样本飞轮","p1"),("安全与权限","RBAC · 脱敏 · 审批","p1"),("成本与配额","用量计量 · 限额","p2")],3),
("数据资产","平台自建、越用越厚的资产",[("元数据 / 表卡片","结构 + 中文释义 + 示例"),("指标与口径库","业务定义的唯一出处"),("问答样本库","已验证的问题→SQL"),("审计与运行日志","合规与归因依据")],4),
("底层依赖","",[("数仓与业务库","PG · MySQL · StarRocks · Hive"),("知识库文档","制度 · 口径 · 手册"),("大模型服务","阿里云百炼")],3)]
g2=["通过","发起场景请求","调用平台能力","读写资产","访问底层数据与模型"]
open('fig2.svg','w').write(build(b2,g2,None,None,
 "数据 Agent 平台产品架构：使用者、接触点、产品场景、平台能力、数据资产、底层依赖六层，场景与能力按 MVP、一期、二期三个阶段用实线、虚线、点线区分"))
print("done")
