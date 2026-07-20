# 主站电商 SEO 与内容分层 V1

状态：已实现只读页面库存与职责分层；索引支持本地上传覆盖更新；文章候选生成、主站草稿和发布仍未接入。

## 结论

主站不是博客站的放大版。产品页和分类页承接商业搜索意图，博客文章解释购买前问题、比较和使用场景，并通过内链把用户带回一个明确的产品页或分类页。

```text
首页 → 分类页 → 产品页
                 ↑
文章/购买指南/教程 ─┘
```

## 系统规则

- 产品页：产品名、型号、规格、价格、库存和购买意图。
- 分类页：品类、系列、用途和筛选意图，并直接链接到应收录的产品页。
- 文章：购买指南、对比、教程和问题解答；每篇文章必须有一个转化目标 URL。
- 产品事实、价格、库存、配送和限制声明只允许来自真实站点或产品数据。
- 筛选、排序和参数 URL 默认不是独立内容目标。
- 产品页优先使用 `Product` / `Offer` 数据；文章不能伪装成可购买产品页。
- 主站暂不打开普通博客站的 `strategy_enabled`，不自动发布。

## 已落地

“主站内容”页面读取主站已导入的 `products / collections / pages / posts` 索引，按 URL 识别产品页、分类页、支持文章和其他页面，展示商业目标页、支持层和未扫描库存。站点地图单文件上传默认覆盖旧库存；多文件选择时首个文件覆盖、后续文件合并，并支持 GZip `.gz`。当前只保存解压后的 URL/页面结构，不修改主站数据。

## 研究依据

- [Google：帮助 Google 理解电商网站结构](https://developers.google.com/search/docs/specialty/ecommerce/help-google-understand-your-ecommerce-site-structure)
- [Google：Merchant listing Product 结构化数据](https://developers.google.com/search/docs/appearance/structured-data/merchant-listing)
- [Google：电商分页与增量加载](https://developers.google.com/search/docs/specialty/ecommerce/pagination-and-incremental-page-loading)
- [Shopify：SEO overview](https://help.shopify.com/en/manual/promoting-marketing/seo/seo-overview)
