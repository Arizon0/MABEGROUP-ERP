"""Impostos e despesas operacionais — o que falta para chegar ao lucro real.

Nenhum marketplace conhece o regime tributário nem as despesas fixas do
vendedor. Sem estas duas tabelas, o sistema para na margem de contribuição e
nunca responde "quanto eu realmente lucrei".
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BigPK, Base, TimestampMixin


class RegimeTributario:
    """Como a alíquota é determinada."""

    #: Alíquota informada direto na regra (Lucro Presumido, ICMS-ST, ISS fixo).
    FIXA = "fixed"
    #: Alíquota **efetiva** calculada por faixa de RBT12 — o Simples de verdade.
    SIMPLES_PROGRESSIVO = "simples_progressive"


class BaseImposto:
    """Sobre o que a alíquota incide."""

    RECEITA_BRUTA = "gross"
    BRUTA_MAIS_FRETE = "gross_plus_shipping"
    RECEITA_LIQUIDA = "net"


class TaxRule(Base, TimestampMixin):
    """Regra tributária com vigência.

    A vigência é obrigatória, não um detalhe: no Simples Nacional a alíquota
    efetiva muda conforme o faturamento acumulado dos últimos 12 meses. Uma
    alíquota única aplicada retroativamente reescreveria o imposto de meses já
    apurados e fechados pelo contador.

    O modelo é deliberadamente genérico — uma linha por tributo — em vez de
    embutir as regras do Simples ou do Lucro Presumido no código. Quem conhece
    a apuração é o contador do vendedor, e a legislação muda.
    """

    __tablename__ = "tax_rules"
    __table_args__ = (
        Index("ix_imposto_tenant_vigencia", "tenant_id", "valid_from", "valid_to"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        BigPK, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    #: simples_nacional | lucro_presumido | mei | icms | pis | cofins | outro
    kind: Mapped[str] = mapped_column(String(40), default="simples_nacional")
    #: Alíquota em pontos percentuais (ex.: 8.50 = 8,5%).
    rate_pct: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    base: Mapped[str] = mapped_column(String(30), default=BaseImposto.RECEITA_BRUTA)
    #: Vazio = todos os canais. Preenchido = regra específica de um marketplace.
    channel: Mapped[str] = mapped_column(String(20), default="")
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    #: Nulo = vigente por prazo indeterminado.
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str] = mapped_column(String(500), default="")

    #: ``fixed`` usa ``rate_pct``; ``simples_progressive`` calcula a alíquota
    #: efetiva a partir das faixas de RBT12 em ``tax_brackets``.
    regime: Mapped[str] = mapped_column(String(30), default=RegimeTributario.FIXA)
    #: Rótulo do anexo, só para leitura humana (ex.: "Anexo I — Comércio").
    annex: Mapped[str] = mapped_column(String(60), default="")

    brackets: Mapped[list[TaxBracket]] = relationship(
        back_populates="rule", cascade="all, delete-orphan", order_by="TaxBracket.rbt12_ate"
    )

    def vigente_em(self, quando: date) -> bool:
        if not self.is_active or quando < self.valid_from:
            return False
        return self.valid_to is None or quando <= self.valid_to


class TaxBracket(Base):
    """Faixa da tabela do Simples Nacional.

    As faixas ficam **no banco, editáveis**, e não no código. A tabela do
    Simples muda por lei complementar, e o responsável por conferi-la é o
    contador do vendedor — não um valor compilado que ninguém revisa. O sistema
    conhece a *fórmula*; os números são dado.
    """

    __tablename__ = "tax_brackets"
    __table_args__ = (
        UniqueConstraint("tax_rule_id", "rbt12_ate", name="uq_faixa_regra_teto"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigPK, nullable=False, index=True)
    tax_rule_id: Mapped[int] = mapped_column(
        BigPK, ForeignKey("tax_rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Teto da faixa em receita bruta acumulada de 12 meses.
    rbt12_ate: Mapped[Decimal] = mapped_column(nullable=False)
    #: Alíquota nominal da faixa, em pontos percentuais.
    aliquota_nominal_pct: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    #: Parcela a deduzir, em reais — o que torna a tabela progressiva de fato.
    parcela_deduzir: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)

    rule: Mapped[TaxRule] = relationship(back_populates="brackets")


class CategoriaDespesa:
    ALUGUEL = "rent"
    PESSOAL = "payroll"
    SOFTWARE = "software"
    MARKETING = "marketing"
    EMBALAGEM = "packaging"
    LOGISTICA = "logistics"
    CONTABILIDADE = "accounting"
    IMPOSTOS_FIXOS = "fixed_taxes"
    OUTRA = "other"


class OperatingExpense(Base, TimestampMixin):
    """Despesa operacional lançada por competência.

    Fica **fora** do pedido de propósito: aluguel e salário não pertencem a uma
    venda específica. Entram no DRE do mês, depois da margem de contribuição —
    ratear despesa fixa por pedido produziria um "custo por venda" que muda
    conforme o volume do mês, o que não ajuda ninguém a decidir preço.
    """

    __tablename__ = "operating_expenses"
    __table_args__ = (
        Index("ix_despesa_tenant_competencia", "tenant_id", "competence_month"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        BigPK, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(String(240), nullable=False)
    category: Mapped[str] = mapped_column(String(30), default=CategoriaDespesa.OUTRA)
    amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    #: Primeiro dia do mês de competência — o mês a que a despesa pertence,
    #: independentemente de quando foi paga.
    competence_month: Mapped[date] = mapped_column(Date, nullable=False)
    #: Despesa recorrente é replicada nos meses seguintes pelo job mensal.
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Vazio = despesa da operação toda. Preenchido = atribuída a um canal.
    channel: Mapped[str] = mapped_column(String(20), default="")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[int | None] = mapped_column(BigPK, nullable=True)


class MonthlyClose(Base, TimestampMixin):
    """Fechamento de mês.

    Depois de fechado, o mês não é mais recalculado por chegada de dado
    retroativo (chargeback meses depois, ajuste do canal). Isso protege um
    resultado já entregue ao contador de mudar sozinho — o ajuste entra na
    competência em que aconteceu.
    """

    __tablename__ = "monthly_closes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "month", name="uq_fechamento_tenant_mes"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigPK, nullable=False, index=True)
    month: Mapped[date] = mapped_column(Date, nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_by: Mapped[int | None] = mapped_column(BigPK, nullable=True)
    gross_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    cogs_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    expenses_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    operating_profit: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(String(500), default="")
