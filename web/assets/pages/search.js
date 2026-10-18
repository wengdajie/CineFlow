import { el, icon, segment, table, rankCell, kindLabel, fmtSize, toast, loading, emptyBox, typeLabel, heatCell, iconButton } from '../ui.js';

export function createSearchPage({searchState, SORTERS, resourceActions, siteReportCard, apiStream, api, shell}) {
async function pageSearch() {
  const keyword = el("input", {
    class: "input",
    placeholder: "片名，如：庆余年 / Oppenheimer",
  });
  keyword.value = searchState.keyword;

  const type = el("select", { class: "input" }, [
    el("option", { value: "" }, "全部类型"),
    el("option", { value: "tv" }, "剧集"),
    el("option", { value: "movie" }, "电影"),
    el("option", { value: "anime" }, "动漫"),
  ]);
  const season = el("input", { class: "input", type: "number", placeholder: "季" });
  const episode = el("input", { class: "input", type: "number", placeholder: "集" });
  const results = el("div", {});
  const hotBox = el("div", {});

  const renderResults = () => {
    const filtered = searchState.kind
      ? searchState.items.filter((item) =>
          searchState.kind === "pan"
            ? item.kind === "pan" || item.kind === "direct"
            : item.kind === "torrent" || item.kind === "magnet"
        )
      : searchState.items.slice();
    filtered.sort(SORTERS[searchState.sort] || SORTERS.score);

    const counts = { pan: 0, bt: 0 };
    searchState.items.forEach((item) => {
      if (item.kind === "pan" || item.kind === "direct") counts.pan += 1;
      else counts.bt += 1;
    });

    results.replaceChildren(
      el("div", { class: "card flush" }, [
        el("div", { class: "card-head" }, [
          el("h3", {}, [
            icon("search", "sm"),
            el("span", { text: "搜索结果 " + filtered.length + " / " + searchState.items.length }),
            // 搜索中就把「已回来几个站」显示出来：流式下结果是一批批到的，
            // 不告诉用户还有站没回来，他会以为已经搜完了（而且会少下东西）
            searchState.progress.running
              ? el("span", {
                  class: "tag warn dot",
                  text: "搜索中 " + searchState.progress.received + "/" + searchState.progress.total + " 站",
                })
              : null,
          ]),
          el("div", { class: "row tight center" }, [
            segment(
              [
                { value: "", label: "全部 " + searchState.items.length },
                { value: "bt", label: "BT " + counts.bt },
                { value: "pan", label: "网盘 " + counts.pan },
              ],
              searchState.kind,
              (value) => {
                searchState.kind = value;
                renderResults();
              }
            ),
            segment(
              [
                { value: "score", label: "综合" },
                { value: "seeders", label: "做种" },
                { value: "size", label: "体积" },
                { value: "time", label: "时间" },
              ],
              searchState.sort,
              (value) => {
                searchState.sort = value;
                renderResults();
              }
            ),
          ]),
        ]),
        table(
          [
            { title: "#", render: (row, index) => rankCell(index + 1) },
            {
              title: "资源名称",
              render: (row) =>
                el("div", {}, [
                  el("div", { class: "row tight center" }, [
                    el("div", { class: "truncate", title: row.title, text: row.title }),
                    // 会员正片在列表上就标出来：否则用户只能靠一个个点去试错
                    row.paywalled
                      ? el("span", {
                          class: "tag warn",
                          title: "长视频平台的会员正片，本工具不提供此类抓取，请用平台官方客户端观看",
                          text: "会员",
                        })
                      : null,
                  ]),
                  el("div", { class: "cell-sub" }, [
                    (row.meta && row.meta.quality) || "",
                    (row.meta && row.meta.video_codec) ? " · " + row.meta.video_codec : "",
                    (row.meta && row.meta.effect) ? " · " + row.meta.effect : "",
                    (row.meta && row.meta.episodes && row.meta.episodes.length)
                      ? " · 第 " + row.meta.episodes.join(",") + " 集"
                      : "",
                  ]),
                ]),
            },
            { title: "来源", render: (row) => el("span", { class: "tag", text: row.site || "-" }) },
            {
              title: "类型",
              render: (row) =>
                el("span", { class: "tag " + (row.kind === "pan" ? "brand" : "") }, [
                  icon(row.kind === "pan" ? "cloud" : "link", "sm"),
                  el("span", { text: kindLabel(row.kind) }),
                ]),
            },
            { title: "画质", render: (row) => (row.meta && row.meta.resolution) || "-" },
            { title: "大小", class: "num", render: (row) => fmtSize(row.size) },
            { title: "做种", class: "num", render: (row) => row.seeders || "-" },
            {
              title: "评分",
              class: "num",
              render: (row) => el("span", { class: "tag brand", text: String(Math.round(row.score || 0)) }),
            },
            { title: "操作", render: (row) => resourceActions(row) },
          ],
          filtered,
          searchState.progress.running
            ? "正在搜索，结果会陆续出现…"
            : "没有匹配的资源，试试更换关键词或启用更多站点"
        ),
      ])
    );
    // 站点情况紧跟结果：结果少的时候，用户第一眼要看到的就是「谁没出货、为什么」
    const report = siteReportCard(searchState.sites, function () {
      if (searchState.keyword) doSearch(searchState.keyword);
    });
    if (report) results.appendChild(report);
  };

  const doSearch = async (value) => {
    const text = (value || keyword.value).trim();
    if (!text) {
      toast("请输入关键词", "err");
      return;
    }
    keyword.value = text;
    searchState.keyword = text;

    // 换关键词必须取消上一次的流，否则两次搜索的结果会交叉写进同一个列表
    if (searchState.stream) {
      searchState.stream.abort();
      searchState.stream = null;
    }
    searchState.items = [];
    searchState.sites = [];
    searchState.progress = { received: 0, total: 0, running: true };
    results.replaceChildren(loading());

    let firstBatch = true;
    const handle = apiStream(
      "/search/stream",
      {
        body: {
          keyword: text,
          media_type: type.value || null,
          season: season.value ? Number(season.value) : null,
          episode: episode.value ? Number(episode.value) : null,
        },
      },
      function (event) {
        if (event.type === "start") {
          searchState.progress.total = event.total_sites || 0;
          // 先把骨架画出来：用户立刻看到「正在查 N 个站」，而不是空白转圈
          renderResults();
          return;
        }
        if (event.type === "site") {
          searchState.progress.received = event.received || 0;
          searchState.progress.total = event.total_sites || searchState.progress.total;
          if (event.site) searchState.sites.push(event.site);
          const batch = event.items || [];
          if (batch.length) {
            searchState.items = searchState.items.concat(batch);
            if (firstBatch) {
              firstBatch = false;
              // 首批到达就把 loading 换成真实表格
              renderResults();
              return;
            }
          }
          renderResults();
          return;
        }
        if (event.type === "done") {
          searchState.progress.running = false;
          if (event.sites && event.sites.length) searchState.sites = event.sites;
          renderResults();
          loadHot();
          const bad = searchState.sites.filter(function (row) {
            return row.status === "timeout" || row.status === "error" || row.status === "skipped";
          }).length;
          const total = event.total || searchState.items.length;
          toast(
            "找到 " + total + " 条资源" +
              (bad ? "（" + bad + " 个站点未出货，见下方站点情况）" : ""),
            total ? (bad ? "warn" : "ok") : "warn"
          );
          return;
        }
        if (event.type === "error") {
          searchState.progress.running = false;
          toast(event.message || "搜索失败", "err");
          renderResults();
        }
      }
    );
    searchState.stream = handle;
    try {
      await handle.done;
    } catch (error) {
      if (error && error.name === "AbortError") return;  // 用户主动换词，不是故障
      searchState.progress.running = false;
      // 已经拿到部分结果时不要清屏：保住已出货的站点，只提示出错
      if (searchState.items.length) {
        renderResults();
        toast(error.message, "err");
      } else {
        results.replaceChildren(el("div", { class: "card" }, [emptyBox(error.message, "alert")]));
      }
    } finally {
      if (searchState.stream === handle) searchState.stream = null;
    }
  };

  /** 热度排行侧栏：搜索页直接可见的榜单，点击即搜。 */
  const loadHot = async () => {
    try {
      const response = await api("/trending?limit=8&days=14");
      const data = response.data;
      const hot = data.resources.items;
      const words = data.keywords.items;

      hotBox.replaceChildren(
        el("div", { class: "grid cols-2" }, [
          el("div", { class: "card flush" }, [
            el("div", { class: "card-head" }, [
              el("h3", {}, [icon("flame", "sm"), el("span", { text: "资源热度榜" })]),
              el("span", { class: "tag", text: "近 " + data.resources.window_days + " 天" }),
            ]),
            hot.length
              ? table(
                  [
                    { title: "#", render: (row) => rankCell(row.rank) },
                    {
                      title: "作品",
                      render: (row) => {
                        const link = el("a", {
                          href: "javascript:void(0)",
                          class: "truncate",
                          title: "点击搜索 " + row.title,
                          text: row.title,
                          onclick: () => doSearch(row.title),
                        });
                        return el("div", {}, [
                          link,
                          el("div", { class: "cell-sub", text:
                            typeLabel(row.media_type) +
                            (row.season ? " · 第 " + row.season + " 季" : "") +
                            (row.latest_episode ? " · 更新至 " + row.latest_episode + " 集" : "") +
                            " · " + row.site_count + " 站 " + row.resource_count + " 条" }),
                        ]);
                      },
                    },
                    { title: "热度", render: (row) => heatCell(row.heat_percent, Math.round(row.heat)) },
                  ],
                  hot
                )
              : emptyBox("搜索过的资源会在这里聚合成热度榜", "flame"),
          ]),
          el("div", { class: "card flush" }, [
            el("div", { class: "card-head" }, [
              el("h3", {}, [icon("chart", "sm"), el("span", { text: "搜索热词" })]),
              el("span", { class: "tag", text: "近 " + data.keywords.window_days + " 天" }),
            ]),
            words.length
              ? el(
                  "div",
                  { class: "chips", style: "padding:0 22px 22px" },
                  words.map((item) =>
                    el("button", {
                      class: "chip",
                      text: item.keyword + " · " + item.times,
                      onclick: () => doSearch(item.keyword),
                    })
                  )
                )
              : emptyBox("还没有搜索历史", "search"),
          ]),
        ])
      );
    } catch (error) {
      hotBox.replaceChildren();
    }
  };

  keyword.addEventListener("keydown", (event) => {
    if (event.key === "Enter") doSearch();
  });
  const labeled = (text, node, flex) =>
    el("div", { style: flex ? "flex:" + flex : null }, [
      el("label", { class: "dim tiny", text: text }),
      node,
    ]);

  const searchBtn = el("button", { class: "btn primary", style: "flex:0 0 auto" }, [
    icon("search", "sm"),
    el("span", { text: "搜索" }),
  ]);
  searchBtn.addEventListener("click", () => doSearch());

  shell(
    el("div", { class: "grid" }, [
      el("div", { class: "card" }, [
        el("h3", {}, [icon("search", "sm"), el("span", { text: "聚合搜索（BT 站点 + 网盘）" })]),
        el("div", { class: "row" }, [
          labeled("关键词", keyword, "3"),
          labeled("类型", type),
          labeled("季", season),
          labeled("集", episode),
          searchBtn,
        ]),
      ]),
      results,
      hotBox,
    ]),
    "资源搜索",
    "并发查询所有已启用的索引器与盘搜服务",
    [iconButton("刷新热榜", "refresh", () => loadHot())]
  );
  renderResults();
  loadHot();
  if (searchState.autoSearch) {
    searchState.autoSearch = false;
    doSearch(searchState.keyword);
  }
}
return pageSearch;
}
