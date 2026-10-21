
from fc_clubs_api import FC27API
import json

api = FC27API(platform="common-gen5")

NOME_CLUBE = "Arsenálcøøl FC"


def mostrar(titulo, dados):
    print(f"\n{'=' * 12} {titulo} {'=' * 12}")
    print(json.dumps(
        dados,
        indent=2,
        ensure_ascii=False,
        default=str
    ))


try:
    # Procura o clube pelo nome correto
    resultados = api.search_club_by_name(NOME_CLUBE)
    mostrar("RESULTADOS DA BUSCA", resultados)

    if not resultados:
        print("\nNenhum clube encontrado.")
    else:
        # Confirma o nome antes de consultar as estatísticas
        nome_confirmado = input(
            "\nDigite o ID do clube correto mostrado acima: "
        ).strip()

        clubes = resultados if isinstance(resultados, list) else [resultados]
        clube = next(
            (
                c for c in clubes
                if str(c.get("clubId")) == nome_confirmado
            ),
            None
        )

        if clube is None:
            print("ID não encontrado nos resultados da busca.")
        else:
            CLUBE_ID = clube["clubId"]
            print(f"\nClube selecionado: {clube.get('clubName')}")
            print(f"ID: {CLUBE_ID}")

            consultas = [
                ("DADOS GERAIS", lambda: api.get_club_overall_stats(CLUBE_ID)),
                ("DETALHES DO CLUBE", lambda: api.get_club_details(CLUBE_ID)),
                ("JOGADORES", lambda: api.get_member_stats(CLUBE_ID)),
                ("PARTIDAS DE LIGA", lambda: api.get_club_matches(
                    CLUBE_ID, match_type="leagueMatch", count=10
                )),
                ("AMISTOSOS", lambda: api.get_club_matches(
                    CLUBE_ID, match_type="friendlyMatch", count=10
                )),
                ("PLAYOFFS", lambda: api.get_club_matches(
                    CLUBE_ID, match_type="playoffMatch", count=10
                )),
            ]

            for titulo, consulta in consultas:
                try:
                    mostrar(titulo, consulta())
                except Exception as erro:
                    print(f"\n{titulo}: não foi possível consultar: {erro}")

except Exception as erro:
    print("\nErro ao procurar o clube:", erro)

input("\nPressione Enter para fechar...")
