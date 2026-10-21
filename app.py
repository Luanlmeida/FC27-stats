import json
import os
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template
from fc_clubs_api import FC27API

app = Flask(__name__)
api = FC27API(platform="common-gen5", timezone="America/Sao_Paulo")

CLUBE_ID = int(os.environ.get("CLUBE_ID", 749668))
NOME_CLUBE = "Arsenálcøøl FC"
CACHE_SEGUNDOS = 60
_cache = {}


# ---------- utilidades ----------
def num(v, padrao=0.0):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return padrao


def pick(d, *chaves, padrao=None):
    """Primeira chave existente e não vazia (a API não oficial varia os nomes)."""
    for k in chaves:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return padrao


def consultar(chave, funcao):
    """Consulta a API com cache curto, para não sobrecarregar a EA."""
    agora = time.time()
    if chave in _cache and agora - _cache[chave][0] < CACHE_SEGUNDOS:
        return _cache[chave][1]
    dados = funcao()
    _cache[chave] = (agora, dados)
    return dados


def como_lista(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


# ---------- normalização ----------
def normalizar_jogador(p):
    return {
        "nome": pick(p, "name", "playerName", padrao="-"),
        "pos": pick(p, "proPos", "position", "favoritePosition", padrao="-"),
        "ovr": int(num(pick(p, "proOverall", "overall", padrao=0))),
        "jogos": int(num(pick(p, "gamesPlayed", padrao=0))),
        "gols": int(num(pick(p, "goals", padrao=0))),
        "assist": int(num(pick(p, "assists", padrao=0))),
        "nota": round(num(pick(p, "ratingAve", "averageRating", padrao=0)), 2),
        "mvp": int(num(pick(p, "manOfTheMatch", "mvp", padrao=0))),
    }


def normalizar_partida(m, tipo):
    gols = pick(m, "goals")
    contra = pick(m, "goalsAgainst")
    adversario = pick(m, "opponentName", "opponent")

    # Formato bruto da EA: clubs = {id: {goals, details: {name}}}
    clubs = m.get("clubs") if isinstance(m, dict) else None
    if isinstance(clubs, dict) and str(CLUBE_ID) in clubs:
        meu = clubs[str(CLUBE_ID)]
        outro = next((v for k, v in clubs.items() if k != str(CLUBE_ID)), {})
        gols = pick(meu, "goals", padrao=gols)
        contra = pick(outro, "goals", padrao=contra)
        adversario = pick(outro.get("details", {}), "name", padrao=adversario)

    gols, contra = int(num(gols)), int(num(contra))
    resultado = "win" if gols > contra else "loss" if gols < contra else "draw"

    ts = pick(m, "timestamp")
    try:
        # a biblioteca devolve datetime; o formato bruto da EA usa segundos
        data = (
            ts
            if isinstance(ts, datetime)
            else datetime.fromtimestamp(int(ts), tz=timezone.utc)
        )
        data_txt, ordem = data.strftime("%d/%m/%Y %H:%M"), data.timestamp()
    except (TypeError, ValueError, OSError):
        data_txt, ordem = str(ts or "-"), 0

    return {
        "data": data_txt,
        "ordem": ordem,
        "tipo": tipo,
        "adversario": adversario or "Adversário",
        "gols": gols,
        "contra": contra,
        "resultado": resultado,
        "chaves": chaves(m),
        "jogadores": [],
        "destaque": None,
    }


def chaves(d):
    """Identificadores que ligam uma partida às linhas de jogadores."""
    return [
        str(d[k])
        for k in ("matchId", "match_id", "timestamp")
        if isinstance(d, dict) and d.get(k) not in (None, "")
    ]


def par(a, b):
    """'feitos/tentados' quando os dois existem; senão só o que houver."""
    if a is None:
        return None
    return f"{int(num(a))}/{int(num(b))}" if b is not None else str(int(num(a)))


def normalizar_atuacao(r):
    return {
        "nome": pick(r, "name", "playerName", "playername", padrao="-"),
        "pos": pick(r, "position", "pos", "proPos", padrao="-"),
        "nota": round(num(pick(r, "rating", "ratingAve", padrao=0)), 1),
        "gols": int(num(pick(r, "goals", padrao=0))),
        "assist": int(num(pick(r, "assists", padrao=0))),
        "mvp": num(pick(r, "mom", "manOfTheMatch", "mvp", padrao=0)) > 0,
        "chutes": pick(r, "shots", "shotsTotal"),
        "passes": par(
            pick(r, "passesMade", "passesCompleted", "passes"),
            pick(r, "passAttempts", "passesAttempted"),
        ),
        "desarmes": par(
            pick(r, "tacklesMade", "tackles"),
            pick(r, "tackleAttempts", "tacklesAttempted"),
        ),
        "defesas": pick(r, "saves"),
    }


def montar_painel():
    geral = como_lista(consultar("geral", lambda: api.get_club_overall_stats(CLUBE_ID)))
    s = geral[0] if geral else {}

    try:
        detalhes = consultar("detalhes", lambda: api.get_club_details(CLUBE_ID))
        detalhes = como_lista(detalhes)[0] if detalhes else {}
    except Exception:
        detalhes = {}

    jogadores = [
        normalizar_jogador(p)
        for p in como_lista(
            consultar("membros", lambda: api.get_member_stats(CLUBE_ID))
        )
    ]

    partidas = []
    for tipo, rotulo in (
        ("leagueMatch", "Liga"),
        ("playoffMatch", "Playoff"),
        ("friendlyMatch", "Amistoso"),
    ):
        try:
            lote = consultar(
                tipo,
                lambda t=tipo: api.get_club_matches(CLUBE_ID, match_type=t, count=10),
            )
            do_tipo = [normalizar_partida(m, rotulo) for m in como_lista(lote)]
        except Exception:
            continue  # tipo indisponível: segue com os demais
        # desempenho de cada jogador em cada partida
        try:
            linhas = como_lista(
                consultar(
                    "jog_" + tipo,
                    lambda t=tipo: api.get_match_players(
                        CLUBE_ID, match_type=t, count=10
                    ),
                )
            )
        except Exception:
            linhas = []
        indice = {}
        for linha in linhas:
            atuacao = normalizar_atuacao(linha)
            for k in chaves(linha):
                indice.setdefault(k, []).append(atuacao)
        for m in do_tipo:
            achados = next((indice[k] for k in m["chaves"] if k in indice), [])
            m["jogadores"] = sorted(achados, key=lambda x: -x["nota"])
            m["destaque"] = m["jogadores"][0] if m["jogadores"] else None
        partidas += do_tipo
    partidas.sort(key=lambda m: m["ordem"], reverse=True)

    v = int(num(pick(s, "wins", padrao=0)))
    e = int(num(pick(s, "ties", padrao=0)))
    d = int(num(pick(s, "losses", padrao=0)))
    gm = int(num(pick(s, "goals", padrao=0)))
    gs = int(num(pick(s, "goalsAgainst", padrao=0)))
    jogos = int(num(pick(s, "gamesPlayed", padrao=v + e + d))) or (v + e + d)
    aproveitamento = round(v / jogos * 100, 1) if jogos else 0

    resumo = {
        "jogos": jogos,
        "v": v,
        "e": e,
        "d": d,
        "gm": gm,
        "gs": gs,
        "saldo": gm - gs,
        "aproveitamento": aproveitamento,
        "skill": pick(s, "skillRating", padrao="—"),
        "divisao": pick(s, "currentDivision", "bestDivision", padrao="—"),
        "nome": pick(detalhes, "name", "clubName", padrao=NOME_CLUBE),
        # colunas opcionais: só aparecem se a API realmente trouxer o dado
        "extras": {
            k: any(x[k] is not None for m in partidas for x in m["jogadores"])
            for k in ("chutes", "passes", "desarmes", "defesas")
        },
    }

    # Destaques automáticos
    mais = lambda chave: max(jogadores, key=lambda p: p[chave], default=None)
    art, gar = mais("gols"), mais("assist")
    destaques = {
        "artilheiro": art if art and art["gols"] else None,
        "garcom": gar if gar and gar["assist"] else None,
        "total_assist": sum(p["assist"] for p in jogadores),
        "total_mvp": sum(p["mvp"] for p in jogadores),
    }

    # Radar: indicadores de 0 a 100 calculados a partir dos dados disponíveis
    por_jogo = lambda x: x / jogos if jogos else 0
    radar = {
        "Aproveitamento": aproveitamento,
        "Ataque": min(por_jogo(gm) / 4 * 100, 100),
        "Defesa": max(100 - por_jogo(gs) / 4 * 100, 0),
        "Saldo": max(min(50 + por_jogo(gm - gs) * 12.5, 100), 0),
        "Criação": min(por_jogo(destaques["total_assist"]) / 3 * 100, 100),
        "Destaques": min(por_jogo(destaques["total_mvp"]) * 100, 100),
    }
    radar = {k: round(v_, 1) for k, v_ in radar.items()}

    # Evolução: pontos acumulados (3/1/0) em ordem cronológica
    cron = sorted(partidas, key=lambda m: m["ordem"])
    pontos, acumulado = [], 0
    for m in cron:
        acumulado += {"win": 3, "draw": 1}.get(m["resultado"], 0)
        pontos.append(acumulado)

    graficos = {
        "radar": radar,
        "wdl": [v, e, d],
        "partidas": {
            "rotulos": [f"{m['adversario'][:12]}" for m in cron],
            "gols": [m["gols"] for m in cron],
            "contra": [m["contra"] for m in cron],
            "pontos": pontos,
        },
        "artilheiros": sorted(
            [(p["nome"], p["gols"]) for p in jogadores if p["gols"]],
            key=lambda x: -x[1],
        )[:8],
        "assistencias": sorted(
            [(p["nome"], p["assist"]) for p in jogadores if p["assist"]],
            key=lambda x: -x[1],
        )[:8],
    }
    return resumo, jogadores, partidas, destaques, graficos


# ---------- rotas ----------
def renderizar(aba):
    try:
        resumo, jogadores, partidas, destaques, graficos = montar_painel()
        return render_template(
            "painel.html",
            aba=aba,
            r=resumo,
            jogadores=jogadores,
            partidas=partidas,
            d=destaques,
            graficos=graficos,
            clube_id=CLUBE_ID,
            erro=None,
            atualizado=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        )
    except Exception as e:
        return (
            render_template(
                "painel.html",
                aba=aba,
                r={},
                jogadores=[],
                partidas=[],
                d={},
                graficos={},
                clube_id=CLUBE_ID,
                erro=str(e),
                atualizado="-",
            ),
            502,
        )


@app.route("/")
def inicio():
    return renderizar("painel")


@app.route("/graficos")
def graficos():
    return renderizar("graficos")


@app.route("/elenco")
def elenco():
    return renderizar("elenco")


@app.route("/partidas")
def partidas():
    return renderizar("partidas")


@app.route("/medalhas")
def medalhas():
    return renderizar("medalhas")


@app.route("/debug")
def debug():
    """Mostra o JSON bruto da API: útil para ver o que ela realmente retorna."""
    saida = {}
    consultas = {
        "geral": lambda: api.get_club_overall_stats(CLUBE_ID),
        "detalhes": lambda: api.get_club_details(CLUBE_ID),
        "membros": lambda: api.get_member_stats(CLUBE_ID),
        "liga": lambda: api.get_club_matches(
            CLUBE_ID, match_type="leagueMatch", count=3
        ),
        "atuacoes": lambda: api.get_match_players(
            CLUBE_ID, match_type="leagueMatch", count=2
        ),
        "playoff": lambda: api.get_club_matches(
            CLUBE_ID, match_type="playoffMatch", count=3
        ),
        "amistoso": lambda: api.get_club_matches(
            CLUBE_ID, match_type="friendlyMatch", count=3
        ),
    }
    for nome, f in consultas.items():
        try:
            saida[nome] = f()
        except Exception as e:
            saida[nome] = f"erro: {e}"
    resp = app.response_class(
        json.dumps(saida, indent=2, ensure_ascii=False, default=str),
        mimetype="application/json",
    )
    return resp


if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
