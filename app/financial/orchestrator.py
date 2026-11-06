"""Financial context orchestration for BoursaMind-RAG."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from app.auth.security import Principal
from app.financial.intent import classify_financial_intent
from app.financial.memory import InvestorMemoryService
from app.financial.schemas import (
    FinancialContextOut,
    FinancialQueryRequest,
    FinancialQueryResponse,
)
from app.financial.tools import fetch_live_tool_calls
from app.retrieval.service import QueryService
from app.schemas import QueryRequest


class FinancialContextOrchestrator:
    def __init__(self, session: Session) -> None:
        self.session = session

    def answer(
        self, principal: Principal, user_id: str, payload: FinancialQueryRequest
    ) -> FinancialQueryResponse:
        request_id = str(uuid4())
        intent = classify_financial_intent(payload.question, payload.intent)
        structured = payload.structured_context.model_dump(exclude_none=True)
        filters = {"domain": "boursamind"}
        if payload.ticker:
            filters["ticker"] = payload.ticker

        document_response = QueryService(self.session).query(
            principal,
            QueryRequest(
                query=payload.question,
                filters=filters,
                top_k=payload.top_k,
                include_answer=False,
            ),
        )
        memories = InvestorMemoryService(self.session).recall(
            principal, user_id, ticker=payload.ticker, limit=8
        )
        tools = fetch_live_tool_calls(
            intent, user_id=user_id, ticker=payload.ticker, supplied=structured
        )
        docs = document_response.retrieval.get("results", [])
        limitations: list[str] = []
        if document_response.abstained:
            limitations.append(
                document_response.abstain_reason
                or "No official document evidence found."
            )
        limitations.extend(
            call.limitation for call in tools if call.limitation and call.status != "SUPPLIED"
        )

        context = FinancialContextOut(
            company=self._tool_data(tools, "get_company", structured.get("company")),
            market=self._tool_data(tools, "get_market_snapshot", structured.get("market")),
            market_history=self._tool_data(
                tools, "get_market_history", structured.get("market_history")
            ),
            technicals=self._tool_data(
                tools, "get_technical_indicators", structured.get("technicals")
            ),
            fundamentals=self._tool_data(
                tools, "get_financial_ratios", structured.get("fundamentals")
            ),
            liquidity=self._tool_data(tools, "get_liquidity", structured.get("liquidity")),
            documents=docs,
            portfolio=self._tool_data(tools, "get_portfolio", structured.get("portfolio")),
            watchlist=self._tool_data(tools, "get_watchlist", structured.get("watchlist")),
            chart=self._tool_data(tools, "get_chart_context", structured.get("chart")),
            decision_history=self._tool_data(
                tools, "get_decision_room", structured.get("decision_history")
            ),
            paper_trading=self._tool_data(
                tools, "get_paper_account", structured.get("paper_trading")
            ),
            user_memory=memories,
            sources=[c.model_dump(mode="json") for c in document_response.citations],
            known_limitations=limitations,
        )
        evidence_types = self._evidence_types(context, tools)
        answer, abstained, reason = self._compose_answer(
            payload, intent, context, bool(evidence_types)
        )
        return FinancialQueryResponse(
            request_id=request_id,
            intent=intent,
            answer=answer,
            context=context,
            citations=[c.model_dump(mode="json") for c in document_response.citations],
            tools_called=tools,
            evidence_types=evidence_types,
            abstained=abstained,
            abstain_reason=reason,
        )

    @staticmethod
    def _tool_data(tools, name: str, fallback):
        return next((call.data for call in tools if call.name == name and call.data), fallback)

    def _evidence_types(self, context: FinancialContextOut, tools) -> list[str]:
        values: list[str] = []
        for document in context.documents:
            citation = document.get("citation") or {}
            self._append_evidence(
                values,
                "OFFICIAL_DOCUMENT" if citation.get("is_official") else "DOCUMENT_EVIDENCE",
            )
        for call in tools:
            if call.status in {"SUPPLIED", "LIVE"}:
                self._append_evidence(values, call.evidence_type)
        if context.user_memory:
            self._append_evidence(values, "USER_MEMORY")
        if values and "AI_INFERENCE" not in values:
            values.append("AI_INFERENCE")
        return values

    @staticmethod
    def _append_evidence(values: list[str], evidence_type: str) -> None:
        aliases = {
            "OFFICIAL_DOCUMENT": ("OFFICIAL_FACT",),
            "OFFICIAL_MARKET_DATA": ("OFFICIAL_FACT",),
            "DERIVED_METRIC": ("DERIVED_FINANCIAL_METRIC",),
            "PAPER_SIMULATION": ("SIMULATION_RESULT",),
        }
        for value in (evidence_type, *aliases.get(evidence_type, ())):
            if value not in values:
                values.append(value)

    def _compose_answer(
        self,
        payload: FinancialQueryRequest,
        intent: str,
        context: FinancialContextOut,
        has_evidence: bool,
    ) -> tuple[str, bool, str | None]:
        if not payload.include_answer:
            return "", False, None
        if not has_evidence:
            return (
                (
                    "Désolé, je ne trouve pas actuellement assez de données vérifiables "
                    "pour répondre solidement à votre question. Vous pouvez préciser une "
                    "société, un ticker ou une période, puis réessayer."
                ),
                True,
                "Aucune donnée financière exploitable n’était disponible.",
            )

        company = context.company or {}
        identity = (
            payload.company_name
            or company.get("name")
            or company.get("company_name")
            or payload.ticker
            or company.get("ticker")
            or "la valeur étudiée"
        )
        sector = company.get("sector")
        lines = [f"### Analyse de {identity}"]
        if sector:
            lines.append(f"*Secteur : {sector}*" )

        market = context.market or {}
        variation = self._as_float(market.get("variation_pct"))
        volume_ratio = self._as_float(market.get("volume_ratio"))
        lines.extend(["", "**En bref**"])
        if market:
            if variation is None:
                price_reading = "Le dernier cours disponible ne permet pas de mesurer la variation."
            elif abs(variation) < 0.2:
                price_reading = (
                    f"Le titre termine quasiment stable ({self._number_fr(variation, 2)} %)."
                )
            elif variation > 0:
                price_reading = f"Le titre termine en hausse de {self._number_fr(variation, 2)} %."
            else:
                price_reading = f"Le titre termine en baisse de {self._number_fr(variation, 2)} %."
            if volume_ratio is not None and volume_ratio <= 0.25:
                price_reading += (
                    " La participation est très faible par rapport à la moyenne récente, "
                    "ce qui réduit la portée du mouvement observé."
                )
            lines.append(price_reading)
        else:
            lines.append(
                "Aucune donnée de séance exploitable n’est disponible pour résumer le marché."
            )

        lines.extend(["", "**Faits vérifiés**"])

        if market:
            session = (
                market.get("session_date")
                or market.get("latest_session")
                or market.get("as_of")
            )
            close = market.get("close") or market.get("last_close")
            previous_close = market.get("previous_close")
            volume = market.get("volume")
            average_volume = market.get("avg_volume_20")
            turnover = market.get("turnover")
            if close is not None:
                lines.append(
                    f"- Cours de clôture{f' du {session}' if session else ''} : "
                    f"**{self._number_fr(close, 3)} TND**."
                )
            if previous_close is not None and variation is not None:
                lines.append(
                    f"- Clôture précédente : **{self._number_fr(previous_close, 3)} TND** ; "
                    f"variation de séance : **{self._number_fr(variation, 2)} %**."
                )
            if volume is not None:
                lines.append(f"- Volume échangé : **{self._number_fr(volume, 0)} titres**.")
            if average_volume is not None:
                lines.append(
                    "- Volume moyen sur 20 séances : "
                    f"**{self._number_fr(average_volume, 0)} titres**."
                )
            if volume_ratio is not None:
                lines.append(
                    "- Volume du jour / moyenne 20 séances : "
                    f"**{self._number_fr(volume_ratio * 100, 1)} %**."
                )
            if turnover is not None:
                lines.append(
                    f"- Capitaux échangés : **{self._number_fr(turnover, 3)} TND**."
                )
            if market.get("source"):
                lines.append(f"- Source : **{market['source']}**.")

        history = context.market_history or {}
        history_points = history.get("points") or []
        performance = self._as_float(history.get("period_performance_pct"))
        if history_points:
            first_session = history_points[0].get("session_date")
            last_session = history_points[-1].get("session_date")
            history_period = (
                f" ({first_session} → {last_session})"
                if first_session and last_session
                else ""
            )
            lines.append(
                f"- Historique analysé : **{len(history_points)} séances**"
                f"{history_period}."
            )
        if performance is not None:
            lines.append(
                "- Performance sur la période disponible : "
                f"**{self._number_fr(performance, 2)} %**."
            )
        if history.get("ma20") is not None:
            lines.append(
                "- Moyenne mobile 20 séances : "
                f"**{self._number_fr(history['ma20'], 3)} TND**."
            )
        if history.get("ma50") is not None:
            lines.append(
                "- Moyenne mobile 50 séances : "
                f"**{self._number_fr(history['ma50'], 3)} TND**."
            )

        liquidity = context.liquidity or {}
        if liquidity.get("score") is not None:
            label = liquidity.get("label_fr") or liquidity.get("band_fr") or "non classée"
            lines.append(
                f"- Liquidité sur {liquidity.get('range') or 'la période'} : "
                f"**{self._number_fr(liquidity['score'], 1)}/100 — {label}**."
            )
        if liquidity.get("average_daily_turnover") is not None:
            lines.append(
                "- Capitaux moyens échangés par séance : "
                f"**{self._number_fr(liquidity['average_daily_turnover'], 3)} TND**."
            )

        fundamentals = context.fundamentals or {}
        fundamental_values = fundamentals.get("values") or []
        ratios = fundamentals.get("ratios") or []
        for item in [*fundamental_values[:3], *ratios[:2]]:
            if not isinstance(item, dict) or item.get("value") is None:
                continue
            label = item.get("label_fr") or item.get("metric") or item.get("key")
            if not label:
                continue
            unit = item.get("unit") or item.get("currency") or ""
            period = item.get("fiscal_period")
            scope = item.get("statement_scope")
            assurance = item.get("assurance_status")
            suffix = f" {unit}" if unit else ""
            qualifiers = [
                str(value)
                for value in (period, scope, assurance)
                if value and value not in {"UNSPECIFIED", "NOT_STATED"}
            ]
            period_label = f" ({' · '.join(qualifiers)})" if qualifiers else ""
            lines.append(
                f"- {label}{period_label} : **{self._number_fr(item['value'], 2)}{suffix}**."
            )

        if context.documents:
            titles = [
                (row.get("citation") or {}).get("title")
                for row in context.documents[:3]
                if (row.get("citation") or {}).get("title")
            ]
            lines.append(
                f"- {len(context.documents)} source(s) documentaire(s) pertinente(s) retrouvée(s)."
            )
            lines.extend(f"  - {title}" for title in titles)

        lines.extend(["", "**Analyse**"])
        if market and variation is not None:
            if abs(variation) < 0.2:
                lines.append(
                    "Le prix a peu bougé pendant la séance : il n’y a pas de signal directionnel "
                    "fort dans cette seule observation."
                )
            elif variation > 0:
                lines.append(
                    "La séance est positive, mais une seule variation ne suffit pas pour établir "
                    "une tendance durable."
                )
            else:
                lines.append(
                    "La séance est négative, mais une seule variation ne suffit pas pour établir "
                    "une tendance durable."
                )
        if volume_ratio is not None and volume_ratio <= 0.25:
            lines.append(
                "Le volume est très inférieur à sa moyenne récente. Sur une valeur peu échangée, "
                "un cours de clôture peut refléter peu de transactions et l’exécution d’un ordre "
                "important peut demander plusieurs séances."
            )
        close_value = self._as_float(market.get("close") or market.get("last_close"))
        ma20 = self._as_float(history.get("ma20"))
        ma50 = self._as_float(history.get("ma50"))
        if close_value is not None and ma20 is not None:
            if close_value > ma20:
                relation = "au-dessus"
            elif close_value < ma20:
                relation = "en dessous"
            else:
                relation = "au niveau"
            lines.append(
                f"Le cours clôture {relation} de sa moyenne mobile à 20 séances. "
                "Ce repère décrit la position du prix ; il ne prédit pas son évolution."
            )
        if ma20 is not None and ma50 is not None:
            structure = "supérieure" if ma20 > ma50 else "inférieure" if ma20 < ma50 else "égale"
            lines.append(
                f"La moyenne à 20 séances est {structure} à celle de 50 séances, "
                "un constat de tendance passée qui doit être confirmé par les volumes."
            )
        if liquidity.get("exit_difficulty_fr"):
            lines.append(str(liquidity["exit_difficulty_fr"]))
        if market and not fundamental_values and not ratios:
            lines.append(
                "La plateforme ne dispose pas encore des états financiers officiels indexés "
                "nécessaires pour évaluer la rentabilité, la valorisation ou la solidité du bilan."
            )
        elif market and (fundamental_values or ratios):
            lines.append(
                "La séance de marché et les derniers éléments fondamentaux disponibles sont "
                "présentés séparément afin d’éviter de confondre prix observé et valeur économique."
            )
        elif fundamental_values or ratios:
            lines.append(
                "Les derniers éléments fondamentaux disponibles donnent un point de départ, "
                "mais doivent être comparés aux périodes précédentes et au secteur."
            )

        if context.technicals:
            indicators = context.technicals.get("indicators") or []
            labels = [
                item.get("key") or item.get("label_fr")
                for item in indicators[:8]
                if isinstance(item, dict)
            ]
            if labels:
                lines.append(
                    "Indicateurs techniques disponibles : "
                    + ", ".join(str(label) for label in labels if label)
                    + "."
                )

        if intent == "PORTFOLIO_ANALYSIS" and context.portfolio:
            lines.append(
                "Les positions et liquidités enregistrées dans votre portefeuille "
                "ont été prises en compte."
            )
        if intent == "PAPER_TRADE_CONTEXT" and context.paper_trading:
            lines.append(
                "Les ordres et positions de Paper Trading sont traités comme une simulation."
            )

        lines.extend(["", "**Risques à surveiller**"])
        if volume_ratio is not None and volume_ratio <= 0.25:
            lines.append(
                "- **Liquidité :** volume très faible face à la moyenne récente ; prudence sur "
                "la taille et le délai d’exécution d’un ordre."
            )
        if liquidity.get("zero_trading_pct"):
            lines.append(
                "- **Continuité des échanges :** "
                f"{self._number_fr(liquidity['zero_trading_pct'], 1)} % des séances sans échange "
                "sur la période observée."
            )
        if not fundamental_values and not ratios:
            lines.append(
                "- **Information financière :** impossible de mesurer la rentabilité, "
                "l’endettement ou la valorisation sans publications officielles indexées."
            )

        lines.extend(["", "**À vérifier avant toute décision**"])
        lines.append(
            "- L’évolution sur plusieurs séances, les résultats financiers publiés, "
            "l’historique des dividendes et les indicateurs de liquidité."
        )
        if sector and "BANQUE" in str(sector).upper():
            lines.append(
                "- Pour une banque : produit net bancaire, résultat net, ROE, coût du risque, "
                "créances classées et ratios de solvabilité."
            )

        lines.extend(["", "**Limites**"])
        if not context.documents:
            lines.append(
                "- Aucune publication officielle suffisamment pertinente n’a été retrouvée "
                "pour approfondir cette question."
            )
        if not fundamental_values and not ratios:
            lines.append(
                "- Aucun indicateur fondamental exploitable n’est disponible actuellement."
            )
        if market:
            lines.append(
                "- Les cours affichés sont des données de fin de séance ou différées, "
                "pas un carnet d’ordres en temps réel."
            )

        lines.extend(["", "**Confiance et lacunes**"])
        if context.documents and (fundamental_values or ratios) and market:
            lines.append(
                "- **Confiance élevée sur les faits cités :** marché, chiffres structurés "
                "et documents officiels sont disponibles. L’interprétation reste analytique."
            )
        elif fundamental_values or ratios or context.documents:
            lines.append(
                "- **Confiance moyenne :** une partie des faits est vérifiable, mais la "
                "couverture documentaire ou de marché est incomplète."
            )
        else:
            lines.append(
                "- **Confiance faible :** seules des observations de marché limitées sont "
                "disponibles ; aucune conclusion fondamentale robuste n’est possible."
            )
        missing = fundamentals.get("missing") or []
        if missing:
            labels = [
                item.get("label_fr") or item.get("metric")
                for item in missing[:5]
                if isinstance(item, dict)
            ]
            if labels:
                lines.append("- Données officielles manquantes : " + ", ".join(labels) + ".")

        lines.extend(["", "**Sources documentaires**"])
        if context.documents:
            for index, row in enumerate(context.documents[:5], start=1):
                citation = row.get("citation") or {}
                title = citation.get("title") or "Publication officielle"
                page = citation.get("page") or citation.get("page_number")
                url = citation.get("url") or citation.get("source_url")
                detail = f" · p. {page}" if page else ""
                detail += f" · {url}" if url else ""
                lines.append(f"- [{index}] {title}{detail}")
        else:
            lines.append("- Aucune source documentaire qualifiée n’a étayé cette réponse.")

        lines.extend(
            [
                "",
                "Cette analyse est informative et ne constitue ni une recommandation "
                "d’achat ou de vente, ni une prévision de cours.",
            ]
        )
        return "\n".join(lines), False, None

    @staticmethod
    def _number_fr(value, decimals: int) -> str:
        """Format provider numbers for a French-speaking investor."""
        try:
            rendered = f"{float(value):,.{decimals}f}"
        except (TypeError, ValueError):
            return str(value)
        return rendered.replace(",", "\u00a0").replace(".", ",")

    @staticmethod
    def _as_float(value) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
