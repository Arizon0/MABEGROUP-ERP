"""investimento em ads por competencia e escopo

Revision ID: c8f2a91d4e07
Revises: 734efb8728a7
Create Date: 2026-08-27 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = 'c8f2a91d4e07'
down_revision: str | None = '734efb8728a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ad_spends',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('tenant_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('channel', sa.String(length=20), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('scope', sa.String(length=10), nullable=False, server_default='channel'),
        # '' e não NULL: dois NULL não colidem no UNIQUE do Postgres, o que
        # permitiria duplicar o lançamento do canal e dobrar o rateio.
        sa.Column('reference', sa.String(length=80), nullable=False, server_default=''),
        sa.Column('amount', sa.Numeric(precision=18, scale=4), nullable=False, server_default='0'),
        sa.Column('attributed_revenue', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('notes', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'channel', 'year', 'month', 'scope', 'reference',
            name='uq_ads_tenant_competencia_escopo',
        ),
    )
    op.create_index('ix_ads_tenant_competencia', 'ad_spends', ['tenant_id', 'year', 'month'])
    op.create_index(op.f('ix_ad_spends_tenant_id'), 'ad_spends', ['tenant_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_ad_spends_tenant_id'), table_name='ad_spends')
    op.drop_index('ix_ads_tenant_competencia', table_name='ad_spends')
    op.drop_table('ad_spends')
