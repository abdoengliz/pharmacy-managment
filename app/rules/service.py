from __future__ import annotations

import json
from typing import Any

DEFAULT_POLICIES: tuple[dict[str, Any], ...] = (
    {"category":"finance","key":"finance.allow_negative_balance","name":"السماح بالرصيد السالب","type":"boolean","default":False,"description":"يسمح بتنفيذ العمليات التي تجعل رصيد الحساب سالبًا."},
    {"category":"finance","key":"finance.decimal_places","name":"عدد المنازل العشرية","type":"integer","default":2,"description":"عدد المنازل العشرية المعروضة في القيم المالية."},
    {"category":"inventory","key":"inventory.allow_negative_stock","name":"السماح بالمخزون السالب","type":"boolean","default":False,"description":"يسمح بصرف كمية أكبر من الرصيد المتاح."},
    {"category":"inventory","key":"inventory.low_stock_threshold","name":"حد تنبيه المخزون المنخفض","type":"integer","default":10,"description":"الكمية التي يبدأ عندها تنبيه المخزون المنخفض."},
    {"category":"inventory","key":"inventory.expiry_warning_days","name":"أيام تنبيه قرب الصلاحية","type":"integer","default":90,"description":"عدد الأيام قبل انتهاء الصلاحية لإظهار التنبيه."},
    {"category":"sales","key":"sales.max_discount_percent","name":"الحد الأقصى للخصم","type":"decimal","default":15.0,"description":"أقصى نسبة خصم قبل الحاجة لاعتماد خاص."},
    {"category":"sales","key":"sales.require_customer","name":"إلزام اختيار العميل","type":"boolean","default":False,"description":"يمنع حفظ فاتورة البيع دون عميل."},
    {"category":"sales","key":"sales.require_prescription","name":"إلزام الوصفة الطبية","type":"boolean","default":False,"description":"يفرض بيانات الوصفة للأصناف الخاضعة لها."},
    {"category":"purchases","key":"purchases.require_approval","name":"اعتماد المشتريات يدويًا","type":"boolean","default":True,"description":"يرسل مستندات الشراء إلى مسار الاعتماد قبل الاستلام."},
    {"category":"purchases","key":"purchases.allow_partial_receipt","name":"السماح بالاستلام الجزئي","type":"boolean","default":True,"description":"يسمح باستلام جزء من أمر الشراء."},
    {"category":"hr","key":"hr.calculate_lateness","name":"احتساب التأخير","type":"boolean","default":True,"description":"يحتسب التأخير ضمن معالجة الحضور."},
    {"category":"hr","key":"hr.calculate_overtime","name":"احتساب العمل الإضافي","type":"boolean","default":True,"description":"يدخل ساعات العمل الإضافي في الرواتب."},
    {"category":"system","key":"system.session_timeout_minutes","name":"مدة الجلسة بالدقائق","type":"integer","default":60,"description":"مدة الخمول قبل انتهاء جلسة المستخدم."},
    {"category":"system","key":"system.max_login_attempts","name":"الحد الأقصى لمحاولات الدخول","type":"integer","default":5,"description":"عدد المحاولات قبل تطبيق سياسة القفل."},
)


def _serialize(value: Any, data_type: str) -> str:
    if data_type == "boolean":
        return "1" if bool(value) else "0"
    return str(value)


def _parse(value: str, data_type: str) -> Any:
    if data_type == "boolean":
        return value in {"1", "true", "True", "yes", "on"}
    if data_type == "integer":
        return int(value)
    if data_type == "decimal":
        return float(value)
    if data_type == "json":
        return json.loads(value)
    return value


class RulesService:
    @staticmethod
    def seed_defaults(db: Any, now_value: str) -> None:
        for rule in DEFAULT_POLICIES:
            db.execute(
                """INSERT INTO system_policies
                   (category,rule_key,name,value,default_value,data_type,description,is_editable,is_active,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                (rule["category"], rule["key"], rule["name"], _serialize(rule["default"], rule["type"]),
                 _serialize(rule["default"], rule["type"]), rule["type"], rule["description"], 1, 1, now_value, now_value),
            )

    @staticmethod
    def get(db: Any, rule_key: str, default: Any = None) -> Any:
        row = db.execute("SELECT value,data_type FROM system_policies WHERE rule_key=? AND is_active=1", (rule_key,)).fetchone()
        return default if row is None else _parse(row["value"], row["data_type"])

    @staticmethod
    def set(db: Any, rule_key: str, value: Any, user_id: int | None, changed_at: str, reason: str = "") -> tuple[Any, Any]:
        row = db.execute("SELECT * FROM system_policies WHERE rule_key=? AND is_active=1", (rule_key,)).fetchone()
        if row is None:
            raise KeyError(rule_key)
        if not row["is_editable"]:
            raise PermissionError(rule_key)
        old_value = _parse(row["value"], row["data_type"])
        serialized = _serialize(value, row["data_type"])
        new_value = _parse(serialized, row["data_type"])
        db.execute("UPDATE system_policies SET value=?,updated_at=?,updated_by=? WHERE id=?", (serialized, changed_at, user_id, row["id"]))
        db.execute(
            "INSERT INTO policy_change_log(policy_id,rule_key,old_value,new_value,changed_by,changed_at,reason) VALUES(?,?,?,?,?,?,?)",
            (row["id"], rule_key, row["value"], serialized, user_id, changed_at, reason or None),
        )
        return old_value, new_value

    @staticmethod
    def reset(db: Any, rule_key: str, user_id: int | None, changed_at: str, reason: str = "") -> tuple[Any, Any]:
        row = db.execute("SELECT default_value,data_type FROM system_policies WHERE rule_key=?", (rule_key,)).fetchone()
        if row is None:
            raise KeyError(rule_key)
        return RulesService.set(db, rule_key, _parse(row["default_value"], row["data_type"]), user_id, changed_at, reason)
