"""Bootstrap de produção: organização + proprietário, sem demonstração."""
from __future__ import annotations

from sqlalchemy import func, select

from app import bootstrap
from app.core.security import verificar_senha
from app.models.channel import ChannelAccount
from app.models.tenant import Tenant, User


class TestBootstrap:
    async def test_cria_organizacao_e_proprietario(self, engine, db):
        resultado = await bootstrap.executar()
        assert resultado == {"tenants": 1, "usuarios": 1}

        usuario = await db.scalar(select(User))
        assert usuario is not None
        assert usuario.role == "owner"

    async def test_nao_cria_nenhum_dado_de_demonstracao(self, engine, db):
        """A diferença para o seed: produção nasce vazia de contas simuladas."""
        await bootstrap.executar()
        assert (await db.scalar(select(func.count(ChannelAccount.id)))) == 0

    async def test_e_idempotente(self, engine, db):
        await bootstrap.executar()
        segunda = await bootstrap.executar()
        assert segunda == {"tenants": 0, "usuarios": 0}
        assert (await db.scalar(select(func.count(User.id)))) == 1

    async def test_nao_reescreve_senha_ja_trocada(self, engine, db):
        """Reexecutar o boot não pode ressuscitar a senha inicial."""
        from app.core.security import hash_senha

        await bootstrap.executar()
        usuario = await db.scalar(select(User))
        usuario.password_hash = hash_senha("a-que-o-dono-escolheu")
        await db.commit()

        await bootstrap.executar()

        recarregado = await db.scalar(select(User).where(User.id == usuario.id))
        assert verificar_senha("a-que-o-dono-escolheu", recarregado.password_hash)

    async def test_reaproveita_organizacao_existente(self, engine, db, tenant):
        """Com tenant já criado (ex.: migração de dados), só falta o usuário."""
        resultado = await bootstrap.executar()
        assert resultado["tenants"] == 0
        assert resultado["usuarios"] == 1
        usuario = await db.scalar(select(User))
        assert usuario.tenant_id == tenant.id
