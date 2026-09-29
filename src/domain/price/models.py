"""价格领域值对象。"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class Money:
    """带有明确来源币种的金额，禁止把转换后的金额继续标记为原币种。"""

    amount: Decimal
    currency: str

    def __post_init__(self):
        try:
            amount = self.amount if isinstance(self.amount, Decimal) else Decimal(str(self.amount))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("金额必须是可转换为 Decimal 的数值") from exc
        currency = str(self.currency or "").strip().upper()
        if not currency:
            raise ValueError("币种不能为空")
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "currency", currency)
