from datetime import date

from sqlalchemy import func, select

from app.collector import collect
from app.models import CollectRun, Item
from app.relevance import Classification
from app.sources.base import RawItem, SourceError


class FakeSource:
    def __init__(self, name, items=None, error=None):
        self.name, self._items, self._error = name, items or [], error

    def fetch(self, since):
        if self._error:
            raise SourceError(self._error)
        return list(self._items)


RELEVANTE = RawItem(
    "consema", "551/2026", "Resolução CONSEMA 551/2026", "http://x/551.pdf",
    published_at=date(2026, 5, 20),
    summary="Diretrizes para recuperação de áreas mineradas (PRAD)",
    doc_type="Resolução CONSEMA", issuer="CONSEMA - Conselho Estadual do Meio Ambiente",
)
RUIDO = RawItem("doe_rs", "9", "Nomeação de servidor", "http://x/9", doc_type="Atos de Pessoal")


def count(db):
    return db.scalar(select(func.count()).select_from(Item))


def test_grava_so_o_relevante_e_nao_duplica(session_factory):
    src = [FakeSource("consema", [RELEVANTE]), FakeSource("doe_rs", [RUIDO])]
    with session_factory() as db:
        first = collect(db, src, date(2026, 1, 1))
        assert [(r.fetched, r.created) for r in first] == [(1, 1), (1, 0)]
        again = collect(db, src, date(2026, 1, 1))
        assert [r.created for r in again] == [0, 0]
        assert count(db) == 1
        item = db.scalar(select(Item))
        assert item.status == "new" and item.relevance >= 40  # aparece na lista padrao
        assert "Mineração" in item.theme_list


def test_falha_de_uma_fonte_nao_derruba_as_outras(session_factory):
    src = [FakeSource("fepam", error="FEPAM fora do ar"), FakeSource("consema", [RELEVANTE])]
    with session_factory() as db:
        res = collect(db, src, date(2026, 1, 1))
        assert res[0].error == "FEPAM fora do ar" and res[1].created == 1
        runs = {r.source: r for r in db.scalars(select(CollectRun))}
        assert runs["fepam"].error and not runs["consema"].error


def test_classificador_e_usado_so_acima_do_corte(session_factory):
    class Fake:
        def __init__(self):
            self.seen = []

        def classify(self, item, fallback):
            self.seen.append(item.external_id)
            return Classification(91, ["Mineração"], "avaliado", by="claude")

    fake = Fake()
    with session_factory() as db:
        collect(db, [FakeSource("consema", [RELEVANTE]), FakeSource("doe_rs", [RUIDO])], date(2026, 1, 1), classifier=fake)
        item = db.scalar(select(Item))
        assert fake.seen == ["551/2026"]
        assert (item.relevance, item.classified_by) == (91, "claude")
