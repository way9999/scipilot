# 学术期刊/会议注册表

> 根据学科方向自动匹配检索源，提高文献检索的权威性与针对性

## 使用方式

1. 根据 Phase 1 的 Q2（学科方向）匹配对应领域
2. 构建检索query时优先限定到该领域的顶级venue
3. 搜索策略：`site:` 定向 + 关键词组合

---

## 计算机科学 / 人工智能

### 顶级会议 (CCF-A)
| 缩写 | 全称 | 方向 | 检索标识 |
|------|------|------|----------|
| NeurIPS | Neural Information Processing Systems | ML/AI | `site:neurips.cc` |
| ICML | International Conference on Machine Learning | ML | `site:icml.cc` |
| ICLR | International Conference on Learning Representations | DL | `site:openreview.net/group?id=ICLR` |
| CVPR | Computer Vision and Pattern Recognition | CV | `CVPR` |
| ICCV | International Conference on Computer Vision | CV | `ICCV` |
| ACL | Association for Computational Linguistics | NLP | `site:aclanthology.org` |
| SIGMOD | ACM SIGMOD Conference | DB | `site:dl.acm.org SIGMOD` |
| VLDB | Very Large Data Bases | DB | `site:vldb.org` |
| OSDI | Operating Systems Design and Implementation | Sys | `OSDI` |
| SOSP | Symposium on Operating Systems Principles | Sys | `SOSP` |

### 顶级期刊
| 缩写 | 全称 | IF参考 | 检索标识 |
|------|------|--------|----------|
| TPAMI | IEEE Trans. PAMI | ~24 | `site:ieeexplore.ieee.org TPAMI` |
| JMLR | Journal of Machine Learning Research | ~6 | `site:jmlr.org` |
| AIJ | Artificial Intelligence | ~14 | `Artificial Intelligence journal` |

### 预印本
- arXiv cs.* : `site:arxiv.org cs.LG OR cs.CV OR cs.CL`

---

## 材料科学 / 材料工程

### 顶级期刊
| 缩写 | 全称 | IF参考 | 检索标识 |
|------|------|--------|----------|
| Nature Materials | Nature Materials | ~40 | `site:nature.com/nmat` |
| Adv. Mater. | Advanced Materials | ~30 | `site:onlinelibrary.wiley.com Advanced Materials` |
| Acta Mater. | Acta Materialia | ~9 | `Acta Materialia` |
| JACS | J. American Chemical Society | ~15 | `site:pubs.acs.org JACS` |
| Nano Letters | Nano Letters | ~10 | `site:pubs.acs.org Nano Letters` |

### 顶级会议
| 缩写 | 全称 | 检索标识 |
|------|------|----------|
| MRS | Materials Research Society Meeting | `MRS meeting` |
| TMS | The Minerals, Metals & Materials Society | `TMS annual meeting` |

---

## 生物学 / 生命科学

### 顶级期刊
| 缩写 | 全称 | IF参考 | 检索标识 |
|------|------|--------|----------|
| Nature | Nature | ~65 | `site:nature.com` |
| Science | Science | ~55 | `site:science.org` |
| Cell | Cell | ~65 | `site:cell.com` |
| Nature Methods | Nature Methods | ~48 | `site:nature.com/nmeth` |
| PNAS | Proc. Natl. Acad. Sci. | ~12 | `site:pnas.org` |

### 预印本
- bioRxiv: `site:biorxiv.org`
- medRxiv: `site:medrxiv.org`

---

## 能源与动力工程

### 顶级期刊
| 缩写 | 全称 | IF参考 | 检索标识 |
|------|------|--------|----------|
| Energy | Energy | ~9 | `Energy journal Elsevier` |
| Applied Energy | Applied Energy | ~11 | `Applied Energy` |
| Combustion and Flame | Combustion and Flame | ~5 | `Combustion and Flame` |
| IJHMT | Int. J. Heat Mass Transfer | ~5 | `IJHMT` |
| Energy Environ. Sci. | Energy & Environmental Science | ~32 | `site:pubs.rsc.org Energy Environmental Science` |

---

## 物理学

### 顶级期刊
| 缩写 | 全称 | IF参考 | 检索标识 |
|------|------|--------|----------|
| PRL | Physical Review Letters | ~9 | `site:journals.aps.org PRL` |
| Nature Physics | Nature Physics | ~20 | `site:nature.com/nphys` |
| PRX | Physical Review X | ~15 | `site:journals.aps.org PRX` |
| RMP | Reviews of Modern Physics | ~45 | `Reviews of Modern Physics` |

### 预印本
- arXiv physics: `site:arxiv.org physics`

---

## 化学 / 化学工程

### 顶级期刊
| 缩写 | 全称 | IF参考 | 检索标识 |
|------|------|--------|----------|
| JACS | J. American Chemical Society | ~15 | `site:pubs.acs.org JACS` |
| Angew. Chem. | Angewandte Chemie | ~16 | `Angewandte Chemie` |
| Chem. Rev. | Chemical Reviews | ~62 | `Chemical Reviews` |
| Nature Chem. | Nature Chemistry | ~24 | `site:nature.com/nchem` |
| AIChE J. | AIChE Journal | ~4 | `AIChE Journal` |

---

## 经济学 / 管理学

### 顶级期刊 (UTD24 / FT50)
| 缩写 | 全称 | 检索标识 |
|------|------|----------|
| AER | American Economic Review | `American Economic Review` |
| QJE | Quarterly Journal of Economics | `Quarterly Journal of Economics` |
| Econometrica | Econometrica | `Econometrica` |
| MS | Management Science | `Management Science journal` |
| MIS Quarterly | MIS Quarterly | `MIS Quarterly` |
| SMJ | Strategic Management Journal | `Strategic Management Journal` |

---

## 通用数据库

| 数据库 | 覆盖范围 | 检索策略 |
|--------|----------|----------|
| Google Scholar | 全学科 | 默认首选，广度优先 |
| arXiv | 理工科预印本 | `site:arxiv.org {domain}` |
| IEEE Xplore | 电子/计算机/通信 | `site:ieeexplore.ieee.org` |
| ACM DL | 计算机 | `site:dl.acm.org` |
| PubMed | 生物医学 | `site:pubmed.ncbi.nlm.nih.gov` |
| SSRN | 社科/经管 | `site:ssrn.com` |

---

## 适配函数

```
function getVenues(discipline):
    if discipline in VENUE_REGISTRY:
        return VENUE_REGISTRY[discipline]
    else:
        return GENERIC_DATABASES

function buildQuery(keywords, venues):
    siteFilters = venues.map(v => v.searchId).join(" OR ")
    return f"{keywords} ({siteFilters})"
```
