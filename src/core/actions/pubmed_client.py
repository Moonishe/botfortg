"""Асинхронный клиент для NCBI E-utilities (PubMed)."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


def _api_key() -> str | None:
    """Прочитать NCBI API ключ из окружения (необязательно).

    Returns:
        Значение переменной окружения ``NCBI_API_KEY`` или ``None``.
    """
    return os.environ.get("NCBI_API_KEY")


async def _get_client() -> httpx.AsyncClient:
    """Вернуть (создать при необходимости) глобальный HTTP клиент (thread-safe).

    Returns:
        Экземпляр ``httpx.AsyncClient`` с таймаутами по умолчанию.
    """
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = httpx.AsyncClient(
                    base_url=_BASE_URL,
                    timeout=httpx.Timeout(30.0, connect=10.0),
                )
    return _client


async def close_client() -> None:
    """Закрыть глобальный HTTP клиент (вызвать при завершении приложения)."""
    global _client
    async with _client_lock:
        if _client is not None:
            await _client.aclose()
            _client = None


async def search_pubmed(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Поиск статей в PubMed по заданному запросу.

    Args:
        query: Поисковый запрос (термины PubMed).
        max_results: Максимальное количество результатов (по умолчанию 5).

    Returns:
        Список словарей с ключом ``pmid``, например::

            [{"pmid": "12345678"}, {"pmid": "23456789"}, ...]

    Raises:
        httpx.HTTPStatusError: При HTTP ошибке от NCBI API.
        httpx.RequestError: При сетевой ошибке / таймауте.
    """
    client = await _get_client()
    params: dict[str, str] = {
        "db": "pubmed",
        "term": query,
        "retmax": str(max_results),
        "retmode": "json",
    }
    if api_key := _api_key():
        params["api_key"] = api_key

    response = await client.get("esearch.fcgi", params=params)
    response.raise_for_status()
    data = response.json()

    id_list: list[str] = data.get("esearchresult", {}).get("idlist", [])
    return [{"pmid": pmid} for pmid in id_list]


async def fetch_summaries(pmids: list[str]) -> list[dict[str, Any]]:
    """Получить краткую информацию о статьях по списку PMID.

    Args:
        pmids: Список PMID (например, ``["12345678", "23456789"]``).

    Returns:
        Список словарей с метаданными статей.
        Каждый словарь содержит ключи:

        - ``pmid`` — идентификатор статьи
        - ``title`` — заголовок (или ``None``)
        - ``authors`` — список имён авторов (может быть пустым)
        - ``pubdate`` — дата публикации (YYYY или полная, или ``None``)
        - ``journal`` — название журнала (или ``None``)
        - ``doi`` — DOI (или ``None``)
        - ``url`` — ссылка на PubMed

    Raises:
        httpx.HTTPStatusError: При HTTP ошибке от NCBI API.
        httpx.RequestError: При сетевой ошибке / таймауте.
    """
    if not pmids:
        return []

    client = await _get_client()
    params: dict[str, str] = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "json",
    }
    if api_key := _api_key():
        params["api_key"] = api_key

    response = await client.get("esummary.fcgi", params=params)
    response.raise_for_status()
    data = response.json()

    result_map: dict[str, Any] = data.get("result", {})
    # Поле uids содержит список PMID в том же порядке
    uids: list[str] = data.get("result", {}).get("uids", [])

    articles: list[dict[str, Any]] = []
    for pmid in uids:
        item = result_map.get(pmid, {})
        pmid_str = str(item.get("uid", pmid))

        # Авторы: берём name из каждого элемента списка
        raw_authors: list[dict[str, Any]] = item.get("authors", []) or []
        authors: list[str] = [a.get("name", "") for a in raw_authors if a.get("name")]

        # DOI — может быть в виде "doi: 10.xxxx/yyyy" или просто значением
        doi: str | None = None
        elocation_ids: list[dict[str, str]] = item.get("elocationid", []) or []
        for eid in elocation_ids:
            value = eid.get("value", "")
            if value.lower().startswith("doi:"):
                doi = value[4:].strip()
                break
        # Запасной вариант: если elocationid нет, ищем в articleids
        if not doi:
            article_ids: list[dict[str, Any]] = item.get("articleids", []) or []
            for aid in article_ids:
                if aid.get("idtype", "").lower() == "doi":
                    doi = aid.get("value")
                    break

        pubdate: str | None = item.get("pubdate")
        if pubdate and len(pubdate) > 4:
            # Оставляем полную дату, как есть
            pass

        articles.append(
            {
                "pmid": pmid_str,
                "title": item.get("title"),
                "authors": authors,
                "pubdate": pubdate,
                "journal": item.get("source"),
                "doi": doi,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid_str}/",
            }
        )

    return articles


async def fetch_abstract(pmid: str) -> str | None:
    """Получить текст аннотации (abstract) статьи по PMID.

    Args:
        pmid: Идентификатор статьи PubMed.

    Returns:
        Текст аннотации или ``None``, если аннотация отсутствует / произошла ошибка парсинга.

    Raises:
        httpx.HTTPStatusError: При HTTP ошибке от NCBI API.
        httpx.RequestError: При сетевой ошибке / таймауте.
    """
    client = await _get_client()
    params: dict[str, str] = {
        "db": "pubmed",
        "id": pmid,
        "rettype": "abstract",
        "retmode": "xml",
    }
    if api_key := _api_key():
        params["api_key"] = api_key

    response = await client.get("efetch.fcgi", params=params)
    response.raise_for_status()
    xml_bytes = response.content

    # Парсинг XML — CPU-bound, выполняем в потоке
    try:
        root: ElementTree.Element = await asyncio.to_thread(
            ElementTree.fromstring, xml_bytes
        )
    except ElementTree.ParseError:
        logger.warning("Failed to parse XML for PMID=%s", pmid)
        return None

    # Ищем <AbstractText> внутри <PubmedArticle>
    # Пространство имён может отсутствовать, используем полный поиск
    abstract_texts: list[str] = []
    for abstract_text in root.findall(".//AbstractText"):
        # Текст может быть как в атрибуте, так и в .text
        text = abstract_text.text or ""
        # Собираем текст из дочерних элементов (<i>, <b>, <sup>, <sub>) и их tail
        for child in abstract_text:
            if child.text:
                text += child.text
            if child.tail:
                text += child.tail
        text = text.strip()
        if text:
            abstract_texts.append(text)

    if not abstract_texts:
        return None

    return " ".join(abstract_texts)
