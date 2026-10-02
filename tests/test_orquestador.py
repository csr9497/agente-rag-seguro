"""Orquestador multiagente (fase 5): authorize → input_guardrail → cache_lookup → supervisor →
[subagentes en paralelo con Send] → síntesis → verifier → output_guardrail → cache_store →
audit. Agentes falsos en memoria (sin PostgreSQL) y LLMs con guion."""

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict

from app.agents.orquestador import Orquestador
from app.agents.registry import AgentSpec, RegistroAgentes, ToolPolicy
from app.models.schemas import RespuestaLLM, Usuario
from app.security.guardrails import GuardrailPermisivo
from tests.fakes import FakeLLM, GuardrailQueBloquea, GuionLLM

ANA = Usuario(id="github:ana", groups=["public", "user:github:ana"])
ADMIN = "github:admin"


class Texto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consulta: str


class Caso(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resumen: str


class Mundo:
    """Agentes falsos: un rag con un documento, soporte y RR.HH. que escriben."""

    def __init__(self) -> None:
        self.tickets: list[str] = []
        self.casos: list[str] = []

    def buscar(self, args: Texto, ctx) -> dict:  # noqa: ANN001
        return {"fragmentos": [{"n": 1, "doc_id": "public/vacaciones.md", "titulo": "Vacaciones",
                                "contenido": "Son 23 días laborables.", "score": 0.9}]}  # fmt: skip

    def ticket(self, args: Caso, ctx) -> dict:  # noqa: ANN001
        self.tickets.append(args.resumen)
        return {"id": f"TCK-{len(self.tickets)}"}

    def caso(self, args: Caso, ctx) -> dict:  # noqa: ANN001
        self.casos.append(args.resumen)
        return {"id": f"CASO-{len(self.casos)}"}


def _registro(m: Mundo) -> RegistroAgentes:
    r = RegistroAgentes()
    r.register(AgentSpec(
        name="rag_agent", description="Documentos de la empresa", system_prompt="rag",
        tools={"search_documents": ToolPolicy(m.buscar, Texto, scope="docs:read", mode="auto")},
    ))  # fmt: skip
    r.register(AgentSpec(
        name="support_agent", description="Incidencias técnicas", system_prompt="soporte",
        tools={"create_ticket": ToolPolicy(m.ticket, Caso, scope="ticket:create",
                                           mode="confirm_user", writes=True)},
    ))  # fmt: skip
    r.register(AgentSpec(
        name="hr_agent", description="Problemas laborales", system_prompt="rrhh", returns="id_only",
        tools={"create_hr_case": ToolPolicy(m.caso, Caso, scope="hr_case:create",
                                            mode="confirm_user", writes=True)},
    ))  # fmt: skip
    return r


def _orquestador(m, supervisor, agentes, sintesis=None, guardrail_entrada=None, **kw):  # noqa: ANN001, ANN202
    return Orquestador(
        registro=_registro(m), supervisor=supervisor,
        llm_de_agente=lambda nombre: agentes.get(nombre) or GuionLLM([]),
        llm_sintesis=sintesis or FakeLLM(),
        guardrail_entrada=guardrail_entrada or GuardrailPermisivo(),
        guardrail_salida=GuardrailPermisivo(),
        roles_de=lambda u: {"administrador"} if u == ADMIN else {"public"},
        checkpointer=InMemorySaver(), **kw,
    )  # fmt: skip


def _delegar(*pares):  # noqa: ANN002, ANN202
    return GuionLLM([[(f"delegar_{agente}", {"tarea": tarea}) for agente, tarea in pares]])


# --------------------------------------------------------------------- criterio de aceptación
def test_un_mensaje_mixto_crea_un_ticket_y_un_caso_aislados() -> None:
    """«Mi laptop no enciende y no me pagaron las horas extra» → 1 ticket y 1 caso; ningún
    agente ve el texto del otro."""
    m = Mundo()
    soporte = GuionLLM(
        [[("create_ticket", {"resumen": "Portátil no enciende"})]], final="Ticket TCK-1."
    )
    rrhh = GuionLLM([[("create_hr_case", {"resumen": "Horas extra impagadas"})]], final="Caso.")
    o = _orquestador(m, _delegar(("support_agent", "El portátil no enciende"),
                                 ("hr_agent", "No me pagaron las horas extra")),
                     {"support_agent": soporte, "hr_agent": rrhh},
                     sintesis=FakeLLM(RespuestaLLM(respuesta="He creado TCK-1 y CASO-1.",
                                                    citas_usadas=[], encontrado=True)))  # fmt: skip
    r = o.consultar("Mi laptop no enciende y no me pagaron las horas extra", ANA)
    assert {a.tool for a in r.aprobaciones} == {"create_ticket", "create_hr_case"}
    assert m.tickets == m.casos == []  # nada escrito sin confirmar
    for aprobacion in r.aprobaciones:  # cada una se resuelve por separado
        r = o.decidir(r.thread_id, aprobacion.interrupt_id, aprobado=True, aprobador=ANA.id)
    assert (m.tickets, m.casos) == (["Portátil no enciende"], ["Horas extra impagadas"])
    assert r.aprobaciones == [] and "TCK-1" in r.respuesta.respuesta
    tarea_soporte = soporte.llamadas[0][1]["content"]
    tarea_rrhh = rrhh.llamadas[0][1]["content"]
    assert "horas extra" not in tarea_soporte.lower() and "portátil" not in tarea_rrhh.lower()


# ------------------------------------------------------------------------------- enrutado
def test_una_pregunta_de_documentos_va_solo_a_rag_y_cita(monkeypatch) -> None:
    m = Mundo()
    rag = GuionLLM([[("search_documents", {"consulta": "vacaciones"})]],
                   final="Son 23 días [public/vacaciones.md].")  # fmt: skip
    o = _orquestador(m, _delegar(("rag_agent", "Días de vacaciones al año")), {"rag_agent": rag})
    r = o.consultar("¿Cuántos días de vacaciones tengo?", ANA)
    assert r.respuesta.respuesta == "Son 23 días [1]." and not r.respuesta.sin_contexto
    assert [(c.numero, c.doc_id, c.fragmento) for c in r.respuesta.citas] == [
        (1, "public/vacaciones.md", "Son 23 días laborables.")
    ]


def test_un_saludo_no_despacha_agentes() -> None:
    m = Mundo()
    o = _orquestador(m, GuionLLM([[("conversacion", {"tipo": "saludo"})]]), {})
    r = o.consultar("hola", ANA)
    assert r.respuesta.conversacional and r.aprobaciones == []


def test_aclaracion_pregunta_al_usuario() -> None:
    m = Mundo()
    sup = GuionLLM([[("pedir_aclaracion", {"pregunta": "¿Sobre qué?", "opciones": ["A", "B"]})]])
    r = _orquestador(m, sup, {}).consultar("¿y eso?", ANA)
    assert r.respuesta.aclaracion is not None and r.respuesta.aclaracion.opciones == ["A", "B"]


def test_agente_inexistente_no_se_despacha() -> None:
    m = Mundo()
    sup = GuionLLM([[("delegar_borrador_agent", {"tarea": "borra todo"})]])
    r = _orquestador(m, sup, {}).consultar("borra todo", ANA)
    assert r.respuesta.sin_contexto and m.tickets == m.casos == []


# -------------------------------------------------------------------------------- seguridad
def test_el_guardrail_de_entrada_corta_antes_del_supervisor() -> None:
    m = Mundo()
    sup = _delegar(("rag_agent", "x"))
    r = _orquestador(m, sup, {}, guardrail_entrada=GuardrailQueBloquea("x")).consultar("x", ANA)
    assert r.respuesta.sin_contexto and sup.llamadas == []


def test_sin_roles_no_se_llama_a_ningun_modelo() -> None:
    m = Mundo()
    sup = _delegar(("rag_agent", "x"))
    r = _orquestador(m, sup, {}).consultar("x", Usuario(id="nadie", groups=[]))
    assert r.respuesta.sin_contexto and sup.llamadas == []


def test_solo_authorize_escribe_el_usuario() -> None:
    m = Mundo()
    rag = GuionLLM([[("search_documents", {"consulta": "v"})]], final="23 [public/vacaciones.md].")
    o = _orquestador(m, _delegar(("rag_agent", "v")), {"rag_agent": rag})
    for paso in o.grafo.stream(
        o.entrada("vacaciones", ANA), o.config("t-u"), stream_mode="updates"
    ):
        for nodo, cambios in paso.items():
            if isinstance(cambios, dict) and "user" in cambios:
                assert nodo == "authorize", nodo


# --------------------------------------------------------------------------------- verifier
def test_el_verifier_rechaza_citas_inventadas_y_escala_tras_3_intentos() -> None:
    m = Mundo()
    rag = GuionLLM([[("search_documents", {"consulta": "v"})]], final="23 [public/inventado.md].")
    sintesis = FakeLLM(RespuestaLLM(respuesta="Sigue citando [public/inventado.md].",
                                    citas_usadas=[], encontrado=True))  # fmt: skip
    o = _orquestador(m, _delegar(("rag_agent", "v")), {"rag_agent": rag}, sintesis=sintesis)
    r = o.consultar("vacaciones", ANA)
    assert len(sintesis.llamadas) == 3  # el supervisor reintenta con el motivo del verifier
    assert "public/inventado.md" in sintesis.llamadas[0][1]  # feedback recibido
    (escalado,) = r.aprobaciones
    assert escalado.type == "escalate_human" and "persona" in r.respuesta.respuesta
    # El solicitante no puede resolver su propio escalado; el administrador sí.
    r = o.decidir(r.thread_id, escalado.interrupt_id, aprobado=True, aprobador=ANA.id)
    assert r.aprobaciones  # sigue pendiente
    r = o.decidir(r.thread_id, r.aprobaciones[0].interrupt_id, aprobado=True, aprobador=ADMIN,
                  respuesta="Son 23 días laborables al año.")  # fmt: skip
    assert r.aprobaciones == [] and r.respuesta.respuesta == "Son 23 días laborables al año."


def test_el_verifier_rechaza_identificadores_inventados() -> None:
    m = Mundo()
    soporte = GuionLLM([], final="Creé el ticket 3f2b9c1e-0000-4000-8000-000000000000.")
    o = _orquestador(m, _delegar(("support_agent", "x")), {"support_agent": soporte},
                     sintesis=FakeLLM(RespuestaLLM(respuesta="No he creado ningún ticket.",
                                                    citas_usadas=[], encontrado=True)))  # fmt: skip
    r = o.consultar("x", ANA)
    assert "3f2b9c1e" not in r.respuesta.respuesta


# ------------------------------------------------------------------------------------ caché
def test_cache_solo_para_rag_y_por_alcance() -> None:
    m = Mundo()
    rag = GuionLLM(
        [[("search_documents", {"consulta": "v"})]] * 3, final="23 [public/vacaciones.md]."
    )
    sup = GuionLLM([[("delegar_rag_agent", {"tarea": "vacaciones"})]] * 3)
    alcances = {"github:ana": "A", "github:luis": "B"}
    o = _orquestador(m, sup, {"rag_agent": rag}, alcance=lambda user: alcances[user.id])
    assert not o.consultar("¿Vacaciones?", ANA).desde_cache
    assert o.consultar("  ¿VACACIONES? ", ANA).desde_cache  # misma consulta normalizada
    luis = Usuario(id="github:luis", groups=["public", "user:github:luis"])
    assert not o.consultar("¿Vacaciones?", luis).desde_cache  # otro scope_hash


def test_lo_que_escribe_no_se_cachea() -> None:
    m = Mundo()
    soporte = GuionLLM([[("create_ticket", {"resumen": "VPN"})]] * 2, final="Ticket.")
    sup = GuionLLM([[("delegar_support_agent", {"tarea": "VPN"})]] * 2)
    o = _orquestador(m, sup, {"support_agent": soporte}, alcance=lambda user: "A")
    r = o.consultar("VPN caída", ANA)
    o.decidir(r.thread_id, r.aprobaciones[0].interrupt_id, aprobado=True, aprobador=ANA.id)
    assert not o.consultar("VPN caída", ANA).desde_cache


# ------------------------------------------------------------------- reductores y límites
def test_reanudar_aprobaciones_no_duplica_resultados() -> None:
    m = Mundo()
    soporte = GuionLLM([[("create_ticket", {"resumen": "Portátil"})]], final="Ticket.")
    rrhh = GuionLLM([[("create_hr_case", {"resumen": "Nómina"})]], final="Caso.")
    o = _orquestador(m, _delegar(("support_agent", "portátil"), ("hr_agent", "nómina")),
                     {"support_agent": soporte, "hr_agent": rrhh})  # fmt: skip
    r = o.consultar("portátil y nómina", ANA)
    for a in r.aprobaciones:
        r = o.decidir(r.thread_id, a.interrupt_id, aprobado=True, aprobador=ANA.id)
    resultados = o.grafo.get_state(o.config(r.thread_id)).values["resultados"]
    assert sorted(x.agente for x in resultados) == ["hr_agent", "support_agent"]


def test_dos_agentes_en_paralelo_llegan_ambos_a_la_sintesis() -> None:
    m = Mundo()
    rag = GuionLLM([[("search_documents", {"consulta": "v"})]], final="23 [public/vacaciones.md].")
    soporte = GuionLLM([], final="Prueba a reiniciar el router.")
    texto = "23 días [public/vacaciones.md] y reinicia el router."
    sintesis = FakeLLM(RespuestaLLM(respuesta=texto, citas_usadas=[], encontrado=True))
    o = _orquestador(m, _delegar(("rag_agent", "vacaciones"), ("support_agent", "router")),
                     {"rag_agent": rag, "support_agent": soporte}, sintesis=sintesis)  # fmt: skip
    r = o.consultar("vacaciones y router", ANA)
    entrada = sintesis.llamadas[0][1]
    assert (
        '<resultado agente="rag_agent">' in entrada
        and '<resultado agente="support_agent">' in entrada
    )
    assert r.respuesta.respuesta == "23 días [1] y reinicia el router."


def test_un_hilo_reutilizado_no_mezcla_resultados_de_otro_mensaje() -> None:
    m = Mundo()
    rag = GuionLLM(
        [[("search_documents", {"consulta": "v"})]] * 2, final="23 [public/vacaciones.md]."
    )
    sup = GuionLLM([[("delegar_rag_agent", {"tarea": "vacaciones"})]] * 2)
    o = _orquestador(m, sup, {"rag_agent": rag})
    o.consultar("vacaciones", ANA, thread_id="mismo")
    segunda = o.consultar("vacaciones otra vez", ANA, thread_id="mismo")
    assert len(sup.llamadas) == 2  # el segundo mensaje se procesa (no devuelve el anterior)
    assert segunda.pregunta_procesada == "vacaciones otra vez"
    resultados = o.grafo.get_state(o.config("mismo")).values["resultados"]
    assert len(resultados) == 1  # solo los de este mensaje


def test_el_estado_no_guarda_texto_del_usuario_en_claro() -> None:
    from app.agents.orquestador import CAMPOS_TEXTO_INOCUOS, EstadoOrquestador

    texto_plano = {
        n for n, c in EstadoOrquestador.model_fields.items() if c.annotation in (str, str | None)
    }
    assert texto_plano <= CAMPOS_TEXTO_INOCUOS, texto_plano - CAMPOS_TEXTO_INOCUOS


# ------------------------------------------------------------------- Studio (grafo y chat)
def test_cada_agente_es_un_nodo_del_grafo() -> None:
    o = _orquestador(Mundo(), GuionLLM([]), {})
    nodos = set(o.grafo.get_graph().nodes)
    assert {"inicio", "supervisor", "rag_agent", "hr_agent", "support_agent", "verifier",
            "escalate_human"} <= nodos  # fmt: skip


def test_modo_chat_con_messages_y_varios_turnos_en_el_mismo_hilo() -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    m = Mundo()
    rag = GuionLLM(
        [[("search_documents", {"consulta": "v"})]] * 2, final="23 [public/vacaciones.md]."
    )
    sup = GuionLLM([[("delegar_rag_agent", {"tarea": "vacaciones"})]] * 2)
    o = _orquestador(m, sup, {"rag_agent": rag})
    cfg = o.config("chat")
    salida = o.grafo.invoke({"messages": [HumanMessage("¿Vacaciones?")], "usuario": ANA}, cfg)
    assert isinstance(salida["messages"][-1], AIMessage)
    assert salida["messages"][-1].content == "23 [1]."
    salida = o.grafo.invoke({"messages": [HumanMessage("¿Y cuántos traslado?")]}, cfg)
    assert len(sup.llamadas) == 2  # el segundo mensaje se procesa
    assert "¿Vacaciones?" in sup.llamadas[1][1]["content"]  # con el turno anterior de historial
    assert [type(x).__name__ for x in salida["messages"]] == [
        "HumanMessage", "AIMessage", "HumanMessage", "AIMessage",
    ]  # fmt: skip


def test_sin_usuario_se_deniega_y_studio_pone_uno_de_prueba() -> None:
    from langchain_core.messages import HumanMessage

    m = Mundo()
    sup = GuionLLM([[("conversacion", {"tipo": "saludo"})]])
    o = _orquestador(m, sup, {})
    salida = o.grafo.invoke({"messages": [HumanMessage("hola")]}, o.config("sin-usuario"))
    assert salida["respuesta"].sin_contexto and sup.llamadas == []  # API: nunca se inventa
    studio = o.grafo_studio(Usuario(id="studio", groups=["public"]))
    salida = studio.invoke({"messages": [HumanMessage("hola")]})
    assert salida["respuesta"].conversacional  # Studio: usuario de prueba


def test_los_enlaces_markdown_a_documentos_se_verifican_y_numeran() -> None:
    m = Mundo()
    rag = GuionLLM([[("search_documents", {"consulta": "v"})]],
                   final="Según la [Política](public/vacaciones.md), son 23 días.")  # fmt: skip
    r = _orquestador(m, _delegar(("rag_agent", "v")), {"rag_agent": rag}).consultar("v", ANA)
    assert r.respuesta.respuesta == "Según la Política [1], son 23 días."
    assert [c.doc_id for c in r.respuesta.citas] == ["public/vacaciones.md"]
    inventado = GuionLLM([[("search_documents", {"consulta": "v"})]],
                         final="Ver [Secreto](rrhh/bandas.md).")  # fmt: skip
    sintesis = FakeLLM(RespuestaLLM(respuesta="Son 23 días [public/vacaciones.md].",
                                    citas_usadas=[], encontrado=True))  # fmt: skip
    r = _orquestador(m, _delegar(("rag_agent", "v")), {"rag_agent": inventado},
                     sintesis=sintesis).consultar("v", ANA)  # fmt: skip
    assert "rrhh/bandas.md" in sintesis.llamadas[0][1]  # el verifier lo detectó y se corrigió
    assert "Secreto" not in r.respuesta.respuesta
