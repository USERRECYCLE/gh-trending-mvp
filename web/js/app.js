/* 渲染与交互。
 *
 * 所有数据读取都经由 TrendingAdapter（E9）——本文件不得出现 fetch 或直接读取数据
 * 文件的代码。DOM 一律用 createElement + textContent 构建，不用 innerHTML：
 * 仓库描述与 AI 解读都是外部文本，拼接 HTML 会形成注入面。
 */
(function (global) {
  "use strict";

  var A = global.TrendingAdapter;
  var doc = global.document;

  var DEFAULT_WINDOW = "daily";
  var DEFAULT_LANGUAGE = "";
  var PENDING_TEXT = "解读生成中";
  // 语言色点来自抓取到的 GitHub style 值，赋值前再校验一次：
  // 只有十六进制字面量才允许进入 element.style，避免样式注入。
  var HEX_COLOR = /^#[0-9a-f]{3,8}$/i;

  function byId(id) {
    return doc.getElementById(id);
  }

  function showError(message) {
    var banner = byId("error-banner");
    banner.textContent = message;
    banner.hidden = false;
  }

  function formatNumber(value) {
    if (value === null || value === undefined || value === "") {
      return "—";
    }
    var number = Number(value);
    if (!isFinite(number)) {
      return String(value);
    }
    return number.toLocaleString("en-US");
  }

  /* ---------- 路由：hash 形如 #/daily/python，便于分享与逐视图验证 ---------- */

  function parseHash() {
    var raw = (global.location.hash || "").replace(/^#\/?/, "");
    if (!raw) {
      return null;
    }
    var parts = raw.split("/");
    return {
      windowKey: parts[0] || DEFAULT_WINDOW,
      languageKey: parts.length > 1 ? decodeURIComponent(parts[1]) : DEFAULT_LANGUAGE
    };
  }

  function setRoute(windowKey, languageKey) {
    var next = "#/" + windowKey + "/" + encodeURIComponent(languageKey || "");
    if (global.location.hash === next) {
      render();
    } else {
      global.location.hash = next;
    }
  }

  /* ---------- 头部控件 ---------- */

  function renderTabs(windows, currentWindow) {
    var host = byId("window-tabs");
    host.textContent = "";
    windows.forEach(function (item) {
      var button = doc.createElement("button");
      button.type = "button";
      button.className = "tab";
      button.textContent = item.label;
      button.setAttribute("aria-selected", String(item.key === currentWindow));
      button.addEventListener("click", function () {
        setRoute(item.key, currentLanguage());
      });
      host.appendChild(button);
    });
  }

  function renderFilters(languages, currentLanguageKey) {
    var host = byId("language-filters");
    host.textContent = "";
    languages.forEach(function (item) {
      var button = doc.createElement("button");
      button.type = "button";
      button.className = "chip";
      button.textContent = item.label;
      button.setAttribute("aria-pressed", String(item.key === currentLanguageKey));
      button.addEventListener("click", function () {
        setRoute(currentWindow(), item.key);
      });
      host.appendChild(button);
    });
  }

  function renderStats(meta, shown) {
    var host = byId("stats");
    var stats = meta.stats || {};
    var parts = ["本页 " + shown + " 个项目"];
    if (stats.entries) {
      parts.push("全部 " + stats.boards + " 个榜单共 " + stats.entries + " 条");
    }
    if (stats.analyzed !== undefined && stats.entries) {
      parts.push("已生成解读 " + stats.analyzed + " 条");
    }
    if (meta.generatedAt) {
      parts.push("更新于 " + meta.generatedAt);
    }
    host.textContent = parts.join(" · ");
  }

  /* ---------- 卡片 ---------- */

  function buildBlock(title, buildContent) {
    var block = doc.createElement("div");
    block.className = "ai__block";
    var heading = doc.createElement("h4");
    heading.textContent = title;
    block.appendChild(heading);
    block.appendChild(buildContent());
    return block;
  }

  function buildList(items) {
    var list = doc.createElement("ul");
    items.forEach(function (item) {
      var li = doc.createElement("li");
      li.textContent = item;
      list.appendChild(li);
    });
    return list;
  }

  function buildTags(items) {
    var host = doc.createElement("div");
    host.className = "tags";
    items.forEach(function (item) {
      var tag = doc.createElement("span");
      tag.className = "tag";
      tag.textContent = item;
      host.appendChild(tag);
    });
    return host;
  }

  function buildScores(scores) {
    var host = doc.createElement("div");
    host.className = "scores";
    [
      ["创新性", scores.innovation],
      ["实用性", scores.practicality],
      ["学习价值", scores.learning_value]
    ].forEach(function (pair) {
      var span = doc.createElement("span");
      span.className = "score";
      span.appendChild(doc.createTextNode(pair[0] + " "));
      var strong = doc.createElement("b");
      strong.textContent = pair[1] + " / 5";
      span.appendChild(strong);
      host.appendChild(span);
    });
    return host;
  }

  function buildPlain(text) {
    var p = doc.createElement("p");
    p.textContent = text;
    return p;
  }

  function buildAnalysis(analysis) {
    var host = doc.createElement("div");
    host.className = "ai";

    var oneLiner = doc.createElement("p");
    oneLiner.className = "ai__one-liner";
    oneLiner.textContent = analysis.one_liner || "";
    host.appendChild(oneLiner);

    var details = doc.createElement("details");
    details.className = "ai__more";
    var summary = doc.createElement("summary");
    summary.textContent = "核心功能 · 技术栈 · 亮点 · 评分";
    details.appendChild(summary);

    if (analysis.core_features && analysis.core_features.length) {
      details.appendChild(buildBlock("核心功能", function () {
        return buildList(analysis.core_features);
      }));
    }
    if (analysis.tech_stack && analysis.tech_stack.length) {
      details.appendChild(buildBlock("技术栈", function () {
        return buildTags(analysis.tech_stack);
      }));
    }
    if (analysis.highlights) {
      details.appendChild(buildBlock("亮点", function () {
        return buildPlain(analysis.highlights);
      }));
    }
    if (analysis.target_audience) {
      details.appendChild(buildBlock("适合谁", function () {
        return buildPlain(analysis.target_audience);
      }));
    }
    if (analysis.scores) {
      details.appendChild(buildBlock("评分", function () {
        return buildScores(analysis.scores);
      }));
    }

    host.appendChild(details);
    return host;
  }

  function buildPending() {
    var host = doc.createElement("div");
    host.className = "ai ai--pending";
    host.textContent = PENDING_TEXT;
    return host;
  }

  function buildCard(entry) {
    var card = doc.createElement("article");
    card.className = "card";
    card.setAttribute("data-repo", entry.name || "");

    var rank = doc.createElement("div");
    rank.className = "card__rank";
    rank.textContent = entry.rank === null || entry.rank === undefined ? "" : String(entry.rank);
    card.appendChild(rank);

    var body = doc.createElement("div");
    body.className = "card__body";

    var name = doc.createElement("a");
    name.className = "card__name";
    name.href = entry.url || "#";
    name.rel = "noopener";
    name.target = "_blank";
    name.textContent = entry.name || "";
    body.appendChild(name);

    if (entry.description) {
      var desc = doc.createElement("p");
      desc.className = "card__desc";
      desc.textContent = entry.description;
      body.appendChild(desc);
    }

    var meta = doc.createElement("div");
    meta.className = "card__meta";
    if (entry.language) {
      var lang = doc.createElement("span");
      var dot = doc.createElement("span");
      dot.className = "dot";
      if (entry.language_color && HEX_COLOR.test(entry.language_color)) {
        dot.style.backgroundColor = entry.language_color;
      }
      lang.appendChild(dot);
      lang.appendChild(doc.createTextNode(entry.language));
      meta.appendChild(lang);
    }
    var stars = doc.createElement("span");
    stars.textContent = "★ " + formatNumber(entry.stars);
    meta.appendChild(stars);
    if (entry.forks !== null && entry.forks !== undefined) {
      var forks = doc.createElement("span");
      forks.textContent = "Fork " + formatNumber(entry.forks);
      meta.appendChild(forks);
    }
    if (entry.add_stars !== null && entry.add_stars !== undefined) {
      var today = doc.createElement("span");
      today.className = "today";
      today.textContent = "+" + formatNumber(entry.add_stars) + " 本期";
      meta.appendChild(today);
    }
    body.appendChild(meta);

    body.appendChild(entry.analysis ? buildAnalysis(entry.analysis) : buildPending());
    card.appendChild(body);
    return card;
  }

  /* ---------- 渲染入口 ---------- */

  var data = null;
  var current = { windowKey: DEFAULT_WINDOW, languageKey: DEFAULT_LANGUAGE };

  function currentWindow() {
    return current.windowKey;
  }

  function currentLanguage() {
    return current.languageKey;
  }

  function render() {
    var meta = A.meta(data);
    var board = A.getBoard(data, current.windowKey, current.languageKey);

    renderTabs(meta.windows, current.windowKey);
    renderFilters(meta.languages, current.languageKey);
    renderStats(meta, board.length);

    var host = byId("cards");
    host.textContent = "";
    if (!board.length) {
      var empty = doc.createElement("p");
      empty.className = "empty";
      empty.textContent = "这个榜单暂时没有数据";
      host.appendChild(empty);
      return;
    }
    board.forEach(function (entry) {
      host.appendChild(buildCard(entry));
    });
  }

  function applyRoute() {
    var route = parseHash();
    if (route) {
      current = route;
    }
    render();
  }

  function boot() {
    A.load()
      .then(function (loaded) {
        data = loaded;
        var route = parseHash();
        current = route || current;
        /* 用 replaceState 写回规范化的 hash：不触发 hashchange，避免首屏重复渲染，
           同时让地址栏始终是可分享的完整视图链接。 */
        global.history.replaceState(
          null,
          "",
          "#/" + current.windowKey + "/" + encodeURIComponent(current.languageKey || "")
        );
        render();
      })
      .catch(function (error) {
        showError(error && error.message ? error.message : "榜单数据加载失败");
        var host = byId("cards");
        host.textContent = "";
      });
  }

  global.addEventListener("hashchange", applyRoute);

  /* 未捕获的运行期错误也走同一横幅：静态看板宁可显示一条明确提示，也不要留一张
     看不出哪里坏了的空白页。这同时让「页面是否出现异常」成为可断言的信号。 */
  global.addEventListener("error", function (event) {
    if (event && event.message) {
      showError("页面出现异常：" + event.message);
    }
  });

  global.addEventListener("unhandledrejection", function (event) {
    var reason = event && event.reason;
    showError("页面出现异常：" + (reason && reason.message ? reason.message : String(reason)));
  });

  if (doc.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  global.TrendingApp = { render: render, boot: boot };
})(window);
