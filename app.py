import json
import os
import time
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, render_template, request
from fc_clubs_api import FC27API

app = Flask(__name__)
api = FC27API(platform="common-gen5", timezone="America/Sao_Paulo")

CLUBE_ID = int(os.environ.get("CLUBE_ID", 749668))
NOME_CLUBE = "Arsenálcøøl FC"
CACHE_SEGUNDOS = 60
_cache = {}

try:
    from zoneinfo import ZoneInfo
    FUSO = ZoneInfo("America/Sao_Paulo")
except Exception:
    FUSO = timezone(timedelta(hours=-3))
ARQ_HISTORICO = os.environ.get("HISTORICO") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "historico.json")
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


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
        data = ts if isinstance(ts, datetime) else datetime.fromtimestamp(int(ts), tz=timezone.utc)
        data_txt, ordem = data.strftime("%d/%m/%Y %H:%M"), data.timestamp()
    except (TypeError, ValueError, OSError):
        data_txt, ordem = str(ts or "-"), 0

    return {
        "data": data_txt, "ordem": ordem, "tipo": tipo,
        "adversario": adversario or "Adversário",
        "gols": gols, "contra": contra, "resultado": resultado,
        "chaves": chaves(m), "jogadores": [], "destaque": None,
    }


def chaves(d):
    """Identificadores que ligam uma partida às linhas de jogadores."""
    return [str(d[k]) for k in ("matchId", "match_id", "timestamp")
            if isinstance(d, dict) and d.get(k) not in (None, "")]


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
        "passes": par(pick(r, "passesMade", "passesCompleted", "passes"),
                      pick(r, "passAttempts", "passesAttempted")),
        "desarmes": par(pick(r, "tacklesMade", "tackles"),
                        pick(r, "tackleAttempts", "tacklesAttempted")),
        "defesas": pick(r, "saves"),
    }


# ---------- histórico em disco (a API só devolve as últimas partidas) ----------
CAMPOS = ("data", "ordem", "tipo", "adversario", "gols", "contra", "resultado", "chaves", "jogadores")


DATABASE_URL = os.environ.get("DATABASE_URL")  # opcional: banco Postgres (ex.: Neon) para o histórico
ESTADO = {"modo": "banco" if DATABASE_URL else "arquivo", "erro": None}
_SQL_TABELA = "CREATE TABLE IF NOT EXISTS historico (id INTEGER PRIMARY KEY, dados TEXT NOT NULL)"


def _conectar():
    import pg8000.native
    from urllib.parse import unquote, urlparse
    u = urlparse(DATABASE_URL)
    return pg8000.native.Connection(
        user=unquote(u.username or ""), password=unquote(u.password or ""),
        host=u.hostname, port=u.port or 5432, database=(u.path or "/").lstrip("/"),
        ssl_context=True, timeout=15)


def carregar_historico():
    """Devolve (historico, ok). Com ok=False nada é salvo, para nunca apagar um histórico que não deu para ler."""
    if DATABASE_URL:
        try:
            con = _conectar()
            try:
                con.run(_SQL_TABELA)
                linhas = con.run("SELECT dados FROM historico WHERE id = 1")
            finally:
                con.close()
            ESTADO["erro"] = None
            return (json.loads(linhas[0][0]) if linhas else {}), True
        except Exception as e:
            ESTADO["erro"] = f"{type(e).__name__}: {e}"
            return {}, False
    try:
        with open(ARQ_HISTORICO, encoding="utf-8") as f:
            return json.load(f), True
    except FileNotFoundError:
        return {}, True
    except (OSError, ValueError):
        return {}, False


def salvar_historico(hist):
    texto = json.dumps(hist, ensure_ascii=False, default=str)
    if DATABASE_URL:
        try:
            con = _conectar()
            try:
                con.run(_SQL_TABELA)
                con.run("INSERT INTO historico (id, dados) VALUES (1, :d) "
                        "ON CONFLICT (id) DO UPDATE SET dados = EXCLUDED.dados", d=texto)
            finally:
                con.close()
            ESTADO["erro"] = None
        except Exception as e:
            ESTADO["erro"] = f"{type(e).__name__}: {e}"
        return
    try:
        tmp = ARQ_HISTORICO + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(texto)
        os.replace(tmp, ARQ_HISTORICO)
    except OSError:
        pass  # disco somente leitura: segue sem salvar


def mesclar(novas):
    """Acumula as partidas já vistas (arquivo ou banco) e devolve todas, da mais recente à mais antiga."""
    hist, ok = carregar_historico()
    mudou = False
    for m in novas:
        k = f"{m['tipo']}|{(m['chaves'] or [m['ordem']])[0]}"
        if k in hist and not m["jogadores"]:
            m["jogadores"] = hist[k].get("jogadores", [])
        entrada = {c: m[c] for c in CAMPOS}
        if hist.get(k) != entrada:
            hist[k] = entrada
            mudou = True
    if mudou and ok:
        salvar_historico(hist)
    todas = []
    for v in hist.values():
        v = dict(v)
        v["jogadores"] = sorted(v.get("jogadores", []), key=lambda x: -x["nota"])
        v["destaque"] = v["jogadores"][0] if v["jogadores"] else None
        todas.append(v)
    todas.sort(key=lambda m: m["ordem"], reverse=True)
    return todas


# ---------- períodos: todo o tempo, mês e semana ----------
def dt_local(m):
    return datetime.fromtimestamp(m["ordem"], tz=FUSO) if m.get("ordem") else None


def chave_periodo(d, periodo):
    if periodo == "mes":
        return f"{d.year}-{d.month:02d}"
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def rotulo_periodo(k, periodo):
    if periodo == "mes":
        y, mes = k.split("-")
        return f"{MESES[int(mes) - 1].capitalize()} de {y}"
    y, w = k.split("-W")
    ini = datetime.fromisocalendar(int(y), int(w), 1)
    return f"Semana {int(w)} ({ini:%d/%m} a {ini + timedelta(days=6):%d/%m})"


def opcoes_periodo(partidas, periodo):
    ks = sorted({chave_periodo(d, periodo) for m in partidas if (d := dt_local(m))}, reverse=True)
    return [(k, rotulo_periodo(k, periodo)) for k in ks]


def filtrar(partidas, periodo, ref):
    if periodo == "tudo":
        return partidas
    return [m for m in partidas if (d := dt_local(m)) and chave_periodo(d, periodo) == ref]


def agregar_jogadores(sel):
    acc = {}
    for m in sel:
        for x in m["jogadores"]:
            r = acc.setdefault(x["nome"], {"nome": x["nome"], "gols": 0, "assist": 0, "mvp": 0})
            r["gols"] += x["gols"]
            r["assist"] += x["assist"]
            r["mvp"] += int(bool(x["mvp"]))
    return list(acc.values())


def montar_destaques(lista):
    mais = lambda k: max(lista, key=lambda p: p[k], default=None)
    art, gar = mais("gols"), mais("assist")
    return {
        "artilheiro": art if art and art["gols"] else None,
        "garcom": gar if gar and gar["assist"] else None,
        "total_assist": sum(p["assist"] for p in lista),
        "total_mvp": sum(p["mvp"] for p in lista),
        "top_assist": sorted([p for p in lista if p["assist"]], key=lambda p: -p["assist"])[:3],
        "top_mvp": sorted([p for p in lista if p["mvp"]], key=lambda p: -p["mvp"])[:3],
    }


def montar_radar(jogos, v, gm, gs, assist, mvp):
    por = lambda x: x / jogos if jogos else 0
    radar = {
        "Aproveitamento": v / jogos * 100 if jogos else 0,
        "Ataque": min(por(gm) / 4 * 100, 100),
        "Defesa": max(100 - por(gs) / 4 * 100, 0),
        "Saldo": max(min(50 + por(gm - gs) * 12.5, 100), 0),
        "Criação": min(por(assist) / 3 * 100, 100),
        "Destaques": min(por(mvp) * 100, 100),
    }
    return {k: round(x, 1) for k, x in radar.items()}


def montar_painel(periodo="tudo", ref=None):
    geral = como_lista(consultar("geral", lambda: api.get_club_overall_stats(CLUBE_ID)))
    s = geral[0] if geral else {}

    try:
        detalhes = consultar("detalhes", lambda: api.get_club_details(CLUBE_ID))
        detalhes = como_lista(detalhes)[0] if detalhes else {}
    except Exception:
        detalhes = {}

    jogadores = [normalizar_jogador(p) for p in como_lista(
        consultar("membros", lambda: api.get_member_stats(CLUBE_ID)))]

    partidas = []
    for tipo, rotulo in (("leagueMatch", "Liga"), ("playoffMatch", "Playoff"),
                         ("friendlyMatch", "Amistoso")):
        try:
            lote = consultar(tipo, lambda t=tipo: api.get_club_matches(
                CLUBE_ID, match_type=t, count=10))
            do_tipo = [normalizar_partida(m, rotulo) for m in como_lista(lote)]
        except Exception:
            continue  # tipo indisponível: segue com os demais
        # desempenho de cada jogador em cada partida
        try:
            linhas = como_lista(consultar("jog_" + tipo, lambda t=tipo: api.get_match_players(
                CLUBE_ID, match_type=t, count=10)))
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
    partidas = mesclar(partidas)  # acumula o histórico em disco

    # Desempenho geral de cada jogador, somando todas as partidas guardadas
    acc = {}
    for m in sorted(partidas, key=lambda x: x["ordem"]):
        for x in m["jogadores"]:
            d_ = acc.setdefault(x["nome"].lower(), {"jogos": 0, "gols": 0, "assist": 0, "mvp": 0, "notas": []})
            d_["jogos"] += 1
            d_["gols"] += x["gols"]
            d_["assist"] += x["assist"]
            d_["mvp"] += int(bool(x["mvp"]))
            if x["nota"]:
                d_["notas"].append(x["nota"])
    for d_ in acc.values():
        n = d_["notas"]
        d_["media"] = round(sum(n) / len(n), 2) if n else 0
        d_["melhor"], d_["pior"] = max(n, default=0), min(n, default=0)
        d_["gpj"], d_["apj"] = round(d_["gols"] / d_["jogos"], 2), round(d_["assist"] / d_["jogos"], 2)
        d_["forma"] = n[-10:]
    for p in jogadores:
        p["rec"] = acc.get(p["nome"].lower())

    v = int(num(pick(s, "wins", padrao=0)))
    e = int(num(pick(s, "ties", padrao=0)))
    d = int(num(pick(s, "losses", padrao=0)))
    gm = int(num(pick(s, "goals", padrao=0)))
    gs = int(num(pick(s, "goalsAgainst", padrao=0)))
    jogos = int(num(pick(s, "gamesPlayed", padrao=v + e + d))) or (v + e + d)
    aproveitamento = round(v / jogos * 100, 1) if jogos else 0

    resumo = {
        "jogos": jogos, "v": v, "e": e, "d": d, "gm": gm, "gs": gs,
        "saldo": gm - gs, "aproveitamento": aproveitamento,
        "skill": pick(s, "skillRating", padrao="—"),
        "divisao": pick(s, "currentDivision", "bestDivision", padrao="—"),
        "nome": pick(detalhes, "name", "clubName", padrao=NOME_CLUBE),
        # colunas opcionais: só aparecem se a API realmente trouxer o dado
        "extras": {k: any(x[k] is not None for m in partidas for x in m["jogadores"])
                   for k in ("chutes", "passes", "desarmes", "defesas")},
    }

    # Período escolhido (vale para a evolução e para as medalhas)
    periodo = periodo if periodo in ("tudo", "mes", "semana") else "tudo"
    opcoes = opcoes_periodo(partidas, periodo) if periodo != "tudo" else []
    if periodo != "tudo" and ref not in [k for k, _ in opcoes]:
        ref = opcoes[0][0] if opcoes else None
    sel = filtrar(partidas, periodo, ref) if (periodo == "tudo" or ref) else []

    if periodo == "tudo":
        lista, (pj, pv, pe, pd, pgm, pgs) = jogadores, (jogos, v, e, d, gm, gs)
    else:
        lista = agregar_jogadores(sel)
        cont = lambda r: sum(1 for m in sel if m["resultado"] == r)
        pj, pv, pe, pd = len(sel), cont("win"), cont("draw"), cont("loss")
        pgm, pgs = sum(m["gols"] for m in sel), sum(m["contra"] for m in sel)

    destaques = montar_destaques(lista)
    radar = montar_radar(pj, pv, pgm, pgs, destaques["total_assist"], destaques["total_mvp"])

    # Evolução: pontos acumulados (3/1/0) em ordem cronológica
    cron = sorted(sel, key=lambda m: m["ordem"])
    pontos, acumulado = [], 0
    for m in cron:
        acumulado += {"win": 3, "draw": 1}.get(m["resultado"], 0)
        pontos.append(acumulado)

    graficos = {
        "radar": radar,
        "wdl": [pv, pe, pd],
        "partidas": {
            "rotulos": [m["adversario"][:12] for m in cron],
            "gols": [m["gols"] for m in cron],
            "contra": [m["contra"] for m in cron],
            "pontos": pontos,
            "resultados": [m["resultado"] for m in cron],
            "adversarios": [m["adversario"] for m in cron],
        },
        "artilheiros": sorted([(p["nome"], p["gols"]) for p in lista if p["gols"]], key=lambda x: -x[1])[:8],
        "assistencias": sorted([(p["nome"], p["assist"]) for p in lista if p["assist"]], key=lambda x: -x[1])[:8],
        "mvps": sorted([(p["nome"], p["mvp"]) for p in lista if p["mvp"]], key=lambda x: -x[1])[:8],
    }
    rotulo = "Todo o tempo" if periodo == "tudo" else dict(opcoes).get(ref, "Sem partidas registradas")
    info = {"periodo": periodo, "ref": ref, "opcoes": opcoes, "rotulo": rotulo, "n": len(sel)}
    return resumo, jogadores, partidas, destaques, graficos, info


# ---------- rotas ----------
def renderizar(aba):
    try:
        resumo, jogadores, partidas, destaques, graficos, info = montar_painel(
            request.args.get("periodo", "semana" if aba == "medalhas" else "tudo"), request.args.get("ref"))
        return render_template(
            "painel.html", aba=aba, info=info, r=resumo, jogadores=jogadores, partidas=partidas,
            d=destaques, graficos=graficos, clube_id=CLUBE_ID, erro=None,
            atualizado=datetime.now(FUSO).strftime("%d/%m/%Y %H:%M:%S"))
    except Exception as e:
        return render_template(
            "painel.html", aba=aba, info={"periodo": "tudo", "ref": None, "opcoes": [], "rotulo": "", "n": 0}, r={}, jogadores=[], partidas=[], d={}, graficos={},
            clube_id=CLUBE_ID, erro=str(e), atualizado="-"), 502


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
        "liga": lambda: api.get_club_matches(CLUBE_ID, match_type="leagueMatch", count=3),
        "atuacoes": lambda: api.get_match_players(CLUBE_ID, match_type="leagueMatch", count=2),
        "playoff": lambda: api.get_club_matches(CLUBE_ID, match_type="playoffMatch", count=3),
        "amistoso": lambda: api.get_club_matches(CLUBE_ID, match_type="friendlyMatch", count=3),
    }
    for nome, f in consultas.items():
        try:
            saida[nome] = f()
        except Exception as e:
            saida[nome] = f"erro: {e}"
    saida["historico"] = {"modo": ESTADO["modo"], "erro": ESTADO["erro"],
                          "partidas_guardadas": len(carregar_historico()[0])}
    resp = app.response_class(
        json.dumps(saida, indent=2, ensure_ascii=False, default=str),
        mimetype="application/json")
    return resp


if __name__ == "__main__":
    print(f"FC 27 Stats — {NOME_CLUBE} (ID {CLUBE_ID})")
    print("Abra http://127.0.0.1:5000 no navegador.")
    app.run(host="127.0.0.1", port=5000, debug=False)
