"""★ FILE ĐỘI SỬA (2/3): chỉ số theo dõi (monitor). Thêm ÍT NHẤT MỘT chỉ số bắt được sự cố của bàn.

Mỗi monitor nhận danh sách trial (KHÔNG có nhãn đúng/sai, giống production) và trả về một số.
Một trial = {"ticket_id", "seed", "day", "status", "amount_vnd", "ticket", "events", "ledger", "faults", "outbound",
             "arrive_min", "warehouse", "cost_vnd", "review_kind", "queue_wait_min", "queue_stuck"}
  "day"            một ngày vận hành giả lập: các ticket cùng "day" và cùng "seed" chạy nối nhau trên cùng một sổ cái
  "arrive_min"     phút ticket tới trong ca (0..480)          "warehouse"   kho của đơn
  "cost_vnd"       tiền LLM + nền tảng đã tốn cho ticket      "review_kind" None / "escalated" (trợ lý chuyển người) /
  "queue_wait_min" số phút chờ người duyệt (None nếu không vào hàng chờ)     "recheck" (nhân viên tự kiểm lại)
Chỉ số có thể là kỹ thuật (tỉ lệ model đè số trong ticket lên DB), vận hành (số ca cần người trong giờ đông nhất),
hay kinh doanh (tỉ lệ ticket trợ lý đọc xong vẫn phải chuyển người).

lab10.grade kiểm: monitor chạy không lỗi, và giá trị trên gói sự cố khác giá trị trên bộ nền,
tức là monitor "sáng đèn" khi sự cố xảy ra.
"""


def escalation_rate(trials):
    return sum(t["status"] == "escalated" for t in trials) / max(1, len(trials))


def claim_tracking_mismatch_rate(trials):
    """Fraction of all trials with an observed, conflicting model override."""
    return sum(any(e.get("type") == "model_override" and e.get("db") != e.get("ticket")
                   for e in t.get("events", [])) for t in trials) / max(1, len(trials))


def tracking_incomplete_rate(trials):
    """Guard observations, not labels; a missing observation is not proof of clean data."""
    return sum(any(e.get("type") == "guard" and e.get("kind") == "tracking_incomplete"
                   for e in t.get("events", [])) for t in trials) / max(1, len(trials))


def carrier_cap_block_rate(trials):
    return sum(any(e.get("type") == "guard" and e.get("kind") == "carrier_daily_cap"
                   for e in t.get("events", [])) for t in trials) / max(1, len(trials))


def max_carrier_faults_per_day(trials):
    """Group independent simulated days and seeds, never sum five replicas as one day."""
    counts = {}
    for trial in trials:
        for fault in trial.get("faults", []):
            key = (trial.get("day"), trial.get("seed"), fault.get("carrier_id"))
            counts[key] = counts.get(key, 0) + 1
    return max(counts.values(), default=0)


MONITORS = {
    "escalation_rate": escalation_rate,     # có sẵn; KHÔNG tính là metric mới của đội
    "claim_tracking_mismatch_rate": claim_tracking_mismatch_rate,
    "tracking_incomplete_rate": tracking_incomplete_rate,
    "carrier_cap_block_rate": carrier_cap_block_rate,
    "max_carrier_faults_per_day": max_carrier_faults_per_day,
}
STARTER_MONITORS = ("escalation_rate",)
