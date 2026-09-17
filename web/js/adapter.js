/* 数据访问的唯一入口（E9）。
 *
 * 页面与渲染逻辑不得直接读取数据源。迁移到自有服务器时只需替换本文件的实现
 * （把 fetch 静态 JSON 改成调用后端接口），app.js 与页面结构零改动。
 * 这也是 §2.6 Adapter 模式的落点。
 */
(function (global) {
  "use strict";

  var DATA_URL = "data/trending.json";
  var cached = null;

  function load() {
    if (cached) {
      return Promise.resolve(cached);
    }
    return fetch(DATA_URL, { cache: "no-cache" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("榜单数据加载失败（HTTP " + response.status + "）");
        }
        return response.json();
      })
      .then(function (data) {
        cached = data;
        return data;
      });
  }

  function boardKey(windowKey, languageKey) {
    return windowKey + "|" + languageKey;
  }

  function getBoard(data, windowKey, languageKey) {
    var boards = (data && data.boards) || {};
    return boards[boardKey(windowKey, languageKey)] || [];
  }

  function meta(data) {
    data = data || {};
    return {
      generatedAt: data.generated_at || "",
      windows: data.windows || [],
      languages: data.languages || [],
      stats: data.stats || {}
    };
  }

  global.TrendingAdapter = {
    load: load,
    meta: meta,
    getBoard: getBoard,
    boardKey: boardKey
  };
})(window);
