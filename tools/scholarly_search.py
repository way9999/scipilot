"""Google Scholar 搜索工具
基于 scholarly 封装，支持论文搜索、作者画像、引用追踪。
Google Scholar 反爬严格，建议配置代理。
代理设置: 调用 setup_proxy() 或设置环境变量 SCHOLARLY_PROXY。
"""

# Python 3.13 兼容补丁：scholarly 依赖已移除的 inspect.formatargspec
import inspect
if not hasattr(inspect, "formatargspec"):
    inspect.formatargspec = lambda args, varargs=None, varkw=None, defaults=None, \
        kwonlyargs=(), kwonlydefaults={}, annotations={}: \
        inspect.formatargvalues(args, varargs, varkw, defaults)

from scholarly import scholarly, ProxyGenerator
import sys
import os


def setup_proxy(proxy_url: str | None = None, use_free: bool = False):
    """配置代理以避免 Google Scholar 封 IP

    Args:
        proxy_url: HTTP/SOCKS 代理地址，如 "http://127.0.0.1:7890"
        use_free: 使用 scholarly 内置免费代理（不稳定）
    """
    pg = ProxyGenerator()
    if proxy_url:
        pg.SingleProxy(http=proxy_url, https=proxy_url)
    elif use_free:
        pg.FreeProxies()
    else:
        env_proxy = os.environ.get("SCHOLARLY_PROXY")
        if env_proxy:
            pg.SingleProxy(http=env_proxy, https=env_proxy)
        else:
            return  # 不设代理，直连
    scholarly.use_proxy(pg)


def search_papers(query: str, limit: int = 10, year_low: int | None = None,
                  year_high: int | None = None) -> list[dict]:
    """搜索 Google Scholar 论文

    Args:
        query: 搜索关键词
        limit: 返回数量上限
        year_low: 起始年份
        year_high: 截止年份
    """
    results = []
    search_gen = scholarly.search_pubs(query, year_low=year_low, year_high=year_high)
    for i, pub in enumerate(search_gen):
        if i >= limit:
            break
        results.append(_parse_pub(pub))
    return results


def search_author(name: str) -> list[dict]:
    """搜索作者，返回作者画像列表"""
    results = []
    search_gen = scholarly.search_author(name)
    for i, author in enumerate(search_gen):
        if i >= 5:
            break
        results.append({
            "name": author.get("name", ""),
            "affiliation": author.get("affiliation", ""),
            "scholar_id": author.get("scholar_id", ""),
            "citedby": author.get("citedby", 0),
            "interests": author.get("interests", []),
        })
    return results


def get_author_publications(scholar_id: str, limit: int = 20) -> list[dict]:
    """获取指定作者的论文列表"""
    author = scholarly.search_author_id(scholar_id)
    author = scholarly.fill(author, sections=["publications"])
    pubs = author.get("publications", [])[:limit]
    results = []
    for pub in pubs:
        filled = scholarly.fill(pub)
        results.append(_parse_pub(filled))
    return results


def get_citation_count(title: str) -> int | None:
    """快速获取论文引用数"""
    search_gen = scholarly.search_pubs(title)
    try:
        pub = next(search_gen)
        return pub.get("num_citations", 0)
    except StopIteration:
        return None


def _parse_pub(pub: dict) -> dict:
    """解析 scholarly 返回的论文数据"""
    bib = pub.get("bib", {})
    return {
        "title": bib.get("title", ""),
        "authors": bib.get("author", []) if isinstance(bib.get("author"), list)
                   else [bib.get("author", "")] if bib.get("author") else [],
        "year": bib.get("pub_year"),
        "abstract": bib.get("abstract", ""),
        "venue": bib.get("venue", "") or bib.get("journal", "") or bib.get("conference", ""),
        "citation_count": pub.get("num_citations", 0),
        "url": pub.get("pub_url", "") or pub.get("eprint_url", ""),
        "pdf_url": pub.get("eprint_url", ""),
        "source": "google_scholar",
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python scholarly_search.py <搜索关键词>")
        print("提示: 设置环境变量 SCHOLARLY_PROXY=http://host:port 避免被封")
        sys.exit(1)

    setup_proxy()
    query = " ".join(sys.argv[1:])
    print(f"正在搜索 Google Scholar: {query}")
    results = search_papers(query, limit=5)
    for i, r in enumerate(results):
        print(f"\n[{i+1}] {r['title']} ({r['year']})")
        print(f"    作者: {', '.join(r['authors'][:3])}")
        print(f"    引用: {r['citation_count']} | 来源: {r['venue']}")
