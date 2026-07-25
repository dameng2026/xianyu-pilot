"""
通用模块元数据目录（开源版精简）
================================
对应 Java ModuleCatalog.java，仅保留开源版需要的模块：
- 系统配置（通用模型 / 向量模型 / RAG）
- 通知（渠道 + 日志）
- 风控事件
- 系统配置 / 运行日志 / 备份 / 版本
- 闲鱼账号 / 商品 / 订单
- 自动回复规则 / 发货规则 / 卡密分组 / 卡密条目
- 快捷回复 / 定时任务

已移除：users / plans / licenses / opportunity-* / workflow-* / ai-usage / ai-token
       / hot-goods / alerts / files / 生图模型 / 生图类目提示词 / 敏感词策略
       （这些模块对应商业化或已删除的链路）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# ============================================================
# 数据结构
# ============================================================
class ColumnMeta:
    """单列元数据。"""

    def __init__(
        self,
        field: str,
        label: str,
        width: int = 120,
        render_type: str = "text",
    ):
        self.field = field
        self.label = label
        self.width = width
        self.render_type = render_type  # text/tag/bool/image

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prop": self.field,
            "label": self.label,
            "width": self.width,
            "type": self.render_type,
        }


class ModuleMeta:
    """单个模块的元数据。"""

    def __init__(
        self,
        key: str,
        name: str,
        description: str,
        columns: List[ColumnMeta],
    ):
        self.key = key
        self.name = name
        self.description = description
        self.columns = columns

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "columns": [c.to_dict() for c in self.columns],
        }


# ============================================================
# 注册中心
# ============================================================
_module_catalog: Dict[str, ModuleMeta] = {}


def register(
    key: str,
    name: str,
    description: str,
    columns: List[ColumnMeta],
) -> None:
    _module_catalog[key] = ModuleMeta(key, name, description, columns)


def get_module(key: str) -> Optional[ModuleMeta]:
    return _module_catalog.get(key)


def list_modules() -> List[ModuleMeta]:
    return list(_module_catalog.values())


def _parse_columns(specs: List[str]) -> List[ColumnMeta]:
    """解析 'field:label:width:render_type' 格式的字符串列表。

    缺省 width=120，render_type=text。
    """
    result: List[ColumnMeta] = []
    for spec in specs:
        parts = spec.split(":")
        field = parts[0]
        label = parts[1] if len(parts) > 1 else field
        try:
            width = int(parts[2]) if len(parts) > 2 and parts[2] else 120
        except ValueError:
            width = 120
        render_type = parts[3] if len(parts) > 3 and parts[3] else "text"
        result.append(ColumnMeta(field, label, width, render_type))
    return result


# ============================================================
# 初始化所有模块
# ============================================================
def _init_modules() -> None:
    # ----- 通知 -----
    register(
        "notify-channels",
        "通知渠道",
        "邮件、Webhook、飞书、企业微信配置",
        _parse_columns([
            "id:ID:80",
            "channelName:渠道名称:160",
            "channelType:类型:120:tag",
            "target:目标:220",
            "status:状态:100:tag",
            "updatedTime:更新时间:170",
        ]),
    )
    register(
        "notify-logs",
        "通知日志",
        "消息通知、告警通知记录",
        _parse_columns([
            "id:ID:80",
            "channelName:渠道:140",
            "title:标题:200",
            "receiver:接收人:150",
            "sendStatus:状态:100:tag",
            "createdTime:发送时间:170",
        ]),
    )

    # ----- 风控 -----
    register(
        "risk-events",
        "风控事件",
        "账号、任务、登录等风险事件处理",
        _parse_columns([
            "id:ID:80",
            "eventType:事件类型:150",
            "riskLevel:等级:100:tag",
            "username:用户:120",
            "accountName:账号:140",
            "title:标题:220",
            "status:状态:100:tag",
            "createdTime:发生时间:170",
        ]),
    )

    # ----- 系统 -----
    register(
        "system-settings",
        "系统配置",
        "全局配置、密钥、存储、邮件、地图、开关",
        _parse_columns([
            "id:ID:80",
            "settingKey:配置键:180",
            "settingValue:配置值:260",
            "settingGroup:分组:120",
            "isSecret:敏感:90:tag",
            "updatedTime:更新时间:170",
        ]),
    )
    register(
        "runtime",
        "运行日志",
        "节点状态、运行日志、内存、磁盘和线程池",
        _parse_columns([
            "id:ID:80",
            "nodeName:节点:140",
            "nodeIp:IP:130",
            "cpuUsage:CPU:90",
            "memoryUsage:内存:90",
            "diskUsage:磁盘:90",
            "status:状态:100:tag",
            "lastHeartbeatTime:心跳:170",
        ]),
    )
    register(
        "backups",
        "数据备份",
        "MySQL 备份、恢复、下载和保留策略",
        _parse_columns([
            "id:ID:80",
            "backupName:备份名称:180",
            "backupType:类型:110:tag",
            "fileSize:大小:90",
            "status:状态:100:tag",
            "createdTime:创建时间:170",
        ]),
    )
    register(
        "versions",
        "版本管理",
        "系统版本、升级记录和灰度发布",
        _parse_columns([
            "id:ID:80",
            "version:版本号:120",
            "title:标题:200",
            "releaseType:类型:100:tag",
            "status:状态:100:tag",
            "releasedTime:发布时间:170",
        ]),
    )

    # ----- 闲鱼 -----
    register(
        "xianyu-accounts",
        "闲鱼账号",
        "管理绑定的闲鱼账号，包括状态、Cookie/WebSocket/在线状态、会员等级等",
        _parse_columns([
            "id:ID:80",
            "externalUid:闲鱼UID:140",
            "nickname:闲鱼昵称:140",
            "status:账号状态:100:tag",
            "cookieStatus:Cookie状态:120:tag",
            "wsStatus:WebSocket:110:tag",
            "onlineStatus:在线状态:100:tag",
            "accountLevel:会员等级:110:tag",
            "lastLoginTime:最后登录:170",
            "lastSyncTime:最后同步:170",
            "createdTime:创建时间:170",
        ]),
    )
    register(
        "xianyu-goods",
        "闲鱼商品",
        "闲鱼商品列表与上下架状态",
        _parse_columns([
            "id:ID:80",
            "title:商品标题:280",
            "price:价格:100",
            "stock:库存:90",
            "status:状态:100:tag",
            "createdTime:创建时间:170",
        ]),
    )
    register(
        "xianyu-orders",
        "闲鱼订单",
        "闲鱼订单数据与状态追踪",
        _parse_columns([
            "id:ID:80",
            "externalOrderId:订单号:200",
            "buyerName:买家:130",
            "totalAmount:金额:100",
            "orderStatus:订单状态:110:tag",
            "createdTime:创建时间:170",
        ]),
    )

    # ----- 自动化 -----
    register(
        "auto-reply-rules",
        "自动回复规则",
        "自动回复规则、命中统计和效果分析",
        _parse_columns([
            "id:ID:80",
            "ruleName:规则名称:180",
            "matchType:匹配模式:100:tag",
            "replyMode:回复模式:100:tag",
            "priority:优先级:90",
            "status:状态:100:tag",
            "createdTime:创建时间:170",
        ]),
    )
    register(
        "delivery-rules",
        "发货规则",
        "自动发货规则与卡密配置",
        _parse_columns([
            "id:ID:80",
            "ruleName:规则名称:180",
            "deliveryMode:发货模式:110:tag",
            "triggerKeyword:触发关键词:160",
            "status:状态:100:tag",
            "createdTime:创建时间:170",
        ]),
    )
    register(
        "card-groups",
        "卡密分组",
        "卡密分组列表、库存和使用记录",
        _parse_columns([
            "id:ID:80",
            "groupName:分组名称:180",
            "groupType:类型:100:tag",
            "totalCount:总数:90",
            "usedCount:已用:90",
            "availableCount:剩余:90",
            "status:状态:100:tag",
            "createdTime:创建时间:170",
        ]),
    )
    register(
        "card-items",
        "卡密条目",
        "卡密明细列表",
        _parse_columns([
            "id:ID:80",
            "groupId:所属分组:120",
            "cardKey:卡密:240",
            "isUsed:是否已用:90:bool",
            "usedByOrder:使用订单:140",
            "createdTime:创建时间:170",
        ]),
    )
    register(
        "quick-reply",
        "快捷回复",
        "人工点击插入的常用语模板",
        _parse_columns([
            "id:ID:80",
            "title:标题:180",
            "content:内容:280",
            "sortOrder:排序:90",
            "status:状态:100:tag",
            "updatedTime:更新时间:170",
        ]),
    )
    register(
        "scheduled-tasks",
        "定时任务",
        "cron 触发的自动化任务",
        _parse_columns([
            "id:ID:80",
            "taskName:任务名称:180",
            "taskType:类型:120:tag",
            "cronExpression:Cron 表达式:160",
            "status:状态:100:tag",
            "lastRunTime:上次执行:170",
            "nextRunTime:下次执行:170",
            "createdTime:创建时间:170",
        ]),
    )
# 模块加载时自动注册
_init_modules()
