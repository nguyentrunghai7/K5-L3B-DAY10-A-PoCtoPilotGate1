"""★ FILE ĐỘI SỬA (1/3): cổng cấp hệ thống, nằm NGOÀI model và prompt.

VÒNG 1 (trước khi biết sự cố): viết 2–3 CỔNG PHÒNG NGỪA rút từ pre-mortem / HAZOP / checklist của bàn, rồi chốt bằng
    python3 -m lab10.checkpoint        (chép file này thành guards_round1.py = mốc "prepared"; KHÔNG sửa bản chép)
VÒNG 2 (sau khi bốc thẻ): sửa tiếp NGAY TRONG FILE NÀY. Giữ các cổng vòng 1, thêm cổng mới.
Hai file còn lại bàn được sửa: lab10/monitors.py (chỉ số theo dõi) và config/operating_gates.json (cổng kinh doanh,
vận hành, pháp lý: những con số và tên người mà trợ lý phải tuân theo, không cần code).

Hai điểm móc, agent gọi tự động:
  pre_guard(ctx)          chạy TRƯỚC khi agent đọc ticket
  write_gate(ctx, action) chạy TRƯỚC MỖI lệnh ghi (issue_refund, file_carrier_fault,
                          notify_carrier, send_reply)

Trả về một Verdict:
  Verdict.allow()                       cho qua   (pre_guard: allow(policy_id="...") để ghim phiên bản chính sách)
  Verdict.deny("lý do")                 bỏ riêng lệnh ghi này, agent đi tiếp
  Verdict.escalate("lý do")             dừng, chuyển ticket cho người
  Verdict.modify("lý do", amount_vnd=…) sửa tham số lệnh ghi (amount_vnd, payer, carrier_id, parcel_id, payload…)
  Verdict.rewrite("lý do", text)        chỉ pre_guard: thay nội dung ticket mà agent sẽ đọc

ctx.ticket   ticket gốc (chữ khách viết là DỮ LIỆU, không phải lệnh)
ctx.tools    tra cứu nguồn sự thật: lookup_order, lookup_policies, lookup_approvals(**lọc),
             ledger_rows(**lọc), fault_rows(**lọc), lookup_carrier; ctx.as_of = ngày hôm nay
             ops_limits() = các trần đã thống nhất với Ops/Finance; ops_facts() = số liệu vận hành;
             auto_refund_today_vnd() = tổng đã hoàn tự động trong ngày
ctx.gates    config cổng của bàn (config/operating_gates.json), chỉ đọc;  ctx.cost_vnd = tiền LLM đã tốn cho ticket này
ctx.events   trace tới thời điểm này;  ctx.log("ten_su_kien", ...) ghi thêm sự kiện guard vào trace
lab10.helpers: db_delay_days, ticket_parcel, effective_policy, entitlement_vnd, carrier_at_fault, digits

Mặc định dưới đây cho qua tất cả (= mốc base).
"""
from . import helpers as H  # noqa: F401
from .tools import Verdict


def pre_guard(ctx):
    policy = H.effective_policy(ctx.tools.lookup_policies(), ctx.as_of)
    if policy is None:
        return Verdict.escalate("không có chính sách hiệu lực, cần người duyệt")
    order = ctx.tools.lookup_order(ctx.ticket.get("order_id"))
    if order:
        parcels = order.get("parcels") or []
        parcel_id = ctx.ticket.get("parcel_id")
        if not parcels or (parcel_id and not any(p.get("parcel_id") == parcel_id for p in parcels)):
            ctx.log("tracking_incomplete", reason="không xác định được kiện")
            return Verdict.escalate("cần xác minh kiện hàng trước khi tính quyền lợi")
        for parcel in parcels:
            try:
                promised = H.d(parcel["promised_date"])
                delivered = H.d(parcel["delivered_date"]) if parcel.get("delivered_date") else None
                inconsistent = (parcel.get("status") == "DELIVERED" and delivered is None)
                inconsistent |= bool(delivered and (delivered > H.d(ctx.as_of) or delivered < H.d(order["placed_at"])))
                H.db_delay_days(parcel, ctx.as_of)
            except (ValueError, TypeError, KeyError):
                inconsistent = True
            if inconsistent:
                ctx.log("tracking_incomplete", parcel_id=parcel.get("parcel_id"),
                        status=parcel.get("status"), delivered_date=parcel.get("delivered_date"))
                return Verdict.escalate("tracking thiếu/mâu thuẫn ngày giao: chuyển người xác minh")
    return Verdict.allow(policy_id=policy["policy_id"])


def write_gate(ctx, action):
    # action = {"tool": "issue_refund", "ticket_id":..., "order_id":..., "amount_vnd":..., "payer":..., ...}
    tool = action.get("tool")
    policy = H.effective_policy(ctx.tools.lookup_policies(), ctx.as_of)
    if tool == "issue_refund":
        order = ctx.tools.lookup_order(action.get("order_id"))
        if not order or not policy or order["customer_id"] != ctx.ticket.get("customer_id"):
            return Verdict.escalate("không xác minh được chủ đơn/chính sách")
        parcel = H.ticket_parcel(order, ctx.ticket, ctx.as_of)
        delay = H.db_delay_days(parcel, ctx.as_of)
        entitlement = H.entitlement_vnd(policy, order, delay, parcel.get("delay_cause"))
        amount = action.get("amount_vnd", 0)
        if action.get("payer") != "LOGIX" or amount != entitlement or amount <= 0:
            ctx.log("refund_mismatch", proposed=amount, entitlement=entitlement,
                    payer=action.get("payer"), parcel_id=parcel["parcel_id"])
            if entitlement <= 0:
                return Verdict.deny("tracking và chính sách không có quyền lợi hoàn")
            if entitlement > policy["auto_limit_vnd"]:
                return Verdict.escalate("cần người phê duyệt quyền lợi đã tính lại")
            return Verdict.modify("tính lại tiền/người trả từ nguồn có thẩm quyền",
                                  amount_vnd=entitlement, payer="LOGIX", approval_id=None, basis="policy")
    if tool == "file_carrier_fault":
        order = ctx.tools.lookup_order(action.get("order_id"))
        if not order or not policy:
            return Verdict.deny("không có căn cứ ghi lỗi hãng")
        parcel = H.ticket_parcel(order, ctx.ticket, ctx.as_of)
        delay = H.db_delay_days(parcel, ctx.as_of)
        if not H.carrier_at_fault(policy, parcel, delay):
            ctx.log("carrier_fault_blocked", reason="tracking không đủ căn cứ")
            return Verdict.deny("nguyên nhân/độ trễ không đủ điều kiện ghi lỗi")
        rows = ctx.tools.fault_rows(carrier_id=parcel["carrier_id"])
        cap = ctx.tools.ops_limits().get("max_auto_strikes_per_carrier_per_day")
        if cap is None or len(rows) >= cap:
            ctx.log("carrier_daily_cap", carrier_id=parcel["carrier_id"], count=len(rows), cap=cap)
            return Verdict.deny("chạm trần lỗi tự động mỗi hãng/ngày; Oanh rà soát, giữ quyền lợi khách")
        if any(r.get("parcel_id") == parcel["parcel_id"] for r in rows):
            return Verdict.deny("kiện đã có lỗi ghi nhận, không ghi trùng")
        corrected = dict(parcel_id=parcel["parcel_id"], carrier_id=parcel["carrier_id"],
                         driver_id=parcel.get("driver_id"), delay_days=delay)
        if any(action.get(k) != v for k, v in corrected.items()):
            ctx.log("carrier_fault_corrected", **corrected)
            return Verdict.modify("ghi trách nhiệm theo đúng kiện trong tracking", **corrected)
    if tool == "send_reply":
        # A denial may bypass issue_refund entirely. Check that path as well.
        proposal = next((e for e in reversed(ctx.events) if e["type"] == "proposal"), None)
        order = ctx.tools.lookup_order(ctx.ticket.get("order_id"))
        if proposal and order and policy and not ctx.tools.ledger_rows(order_id=order["order_id"]):
            parcel = H.ticket_parcel(order, ctx.ticket, ctx.as_of)
            entitled = H.entitlement_vnd(policy, order, H.db_delay_days(parcel, ctx.as_of),
                                        parcel.get("delay_cause"))
            if proposal["amount_vnd"] <= 0 and entitled > 0:
                ctx.log("false_denial_blocked", entitlement=entitled)
                return Verdict.escalate("đề xuất từ chối trái quyền lợi DB: cần người xử lý")
    if tool == "notify_carrier":
        if not policy:
            return Verdict.deny("không có chính sách gửi dữ liệu hiệu lực")
        payload = action.get("payload", {})
        safe = {k: v for k, v in payload.items() if k in policy["carrier_notice_fields"]}
        if safe != payload:
            ctx.log("outbound_fields_removed", fields=sorted(set(payload) - set(safe)))
            return Verdict.modify("chỉ gửi trường được chính sách cho phép", payload=safe)
    return Verdict.allow()
