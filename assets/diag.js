(function () {
  function $(id) { return document.getElementById(id); }

  function classify(score, topicOk, minScore, bgRatio, useRerank) {
    if (!useRerank) {
      // 无重排：主题门 + 分量纲弱相关
      if (!topicOk) return score >= 0.30 ? "bg" : "drop";
      if (score < 0.30) return "drop";
      return "cite";
    }
    const bgFloor = minScore * bgRatio;
    if (score >= minScore && topicOk) return "cite";
    if (score >= minScore && !topicOk) return "bg";
    if (score >= bgFloor) return "bg";
    return "drop";
  }

  const samples = [
    { name: "强相关 · 主题命中", score: 0.82, topic: true },
    { name: "高分偏题（无词汇重叠）", score: 0.91, topic: false },
    { name: "中等相关", score: 0.36, topic: true },
    { name: "弱相关噪声", score: 0.08, topic: true },
  ];

  function renderGate() {
    const minScore = parseFloat($("minScore").value);
    const bgRatio = parseFloat($("bgRatio").value);
    const topicOk = $("topicOk").checked;
    const useRerank = $("useRerank").checked;
    $("minScoreVal").textContent = minScore.toFixed(2);
    $("bgRatioVal").textContent = bgRatio.toFixed(2);

    const counts = { cite: 0, bg: 0, drop: 0 };
    const rows = samples.map(function (s) {
      const topic = s.name.indexOf("偏题") >= 0 ? false : topicOk;
      // 「高分偏题」样本始终按无重叠展示，便于观察主题门
      const topicForSample = s.topic === false ? false : topic;
      const tier = classify(s.score, topicForSample, minScore, bgRatio, useRerank);
      counts[tier] += 1;
      const label = tier === "cite" ? "引用" : tier === "bg" ? "背景" : "丢弃";
      const cite = tier === "cite" ? "是" : "否";
      return (
        "<tr><td>" + s.name + "</td><td>" + s.score.toFixed(2) +
        "</td><td><span class=\"badge " + tier + "\">" + label +
        "</span></td><td>" + cite + "</td></tr>"
      );
    });
    $("gateTable").innerHTML = rows.join("");
    const total = counts.cite + counts.bg + counts.drop || 1;
    $("tierBar").innerHTML =
      "<span class=\"cite\" style=\"width:" + (counts.cite / total * 100) + "%\"></span>" +
      "<span class=\"bg\" style=\"width:" + (counts.bg / total * 100) + "%\"></span>" +
      "<span class=\"drop\" style=\"width:" + (counts.drop / total * 100) + "%\"></span>";

    const bgFloor = useRerank ? (minScore * bgRatio) : 0.30;
    $("gateNote").textContent =
      "当前引用线 " + minScore.toFixed(2) +
      "，背景线 " + bgFloor.toFixed(2) +
      "；计数 引用 " + counts.cite + " / 背景 " + counts.bg + " / 丢弃 " + counts.drop +
      "。空命中时后端 status 会写明「无库内引用依据」。";
  }

  function renderTiming() {
    const ret = +$("retMs").value;
    const web = +$("webMs").value;
    const ttft = +$("ttftMs").value;
    const gen = +$("genMs").value;
    $("retMsVal").textContent = ret + " ms";
    $("webMsVal").textContent = web + " ms";
    $("ttftMsVal").textContent = ttft + " ms";
    $("genMsVal").textContent = gen + " ms";
    const total = ret + web + ttft + gen;
    const max = Math.max(total, 1);
    const stages = [
      { key: "retrieval_ms", label: "检索", ms: ret },
      { key: "web_ms", label: "联网", ms: web },
      { key: "llm_first_token_ms", label: "首 token", ms: ttft },
      { key: "generation_ms", label: "生成", ms: gen },
    ];
    $("timingBars").innerHTML = stages.map(function (s) {
      const slow = s.key === "llm_first_token_ms" && s.ms >= 30000;
      const pct = Math.max(2, (s.ms / max) * 100);
      return (
        "<div class=\"trow" + (slow ? " slow" : "") + "\">" +
        "<div>" + s.label + "</div>" +
        "<div class=\"track\"><div class=\"fill\" style=\"width:" + pct + "%\"></div></div>" +
        "<div class=\"ms\">" + s.ms + "</div></div>"
      );
    }).join("") +
      "<div class=\"trow\"><div>总耗时</div><div class=\"track\"><div class=\"fill\" style=\"width:100%\"></div></div><div class=\"ms\">" + total + "</div></div>";

    let note = "done 帧 timing 字段与上表同构，可直接对照「前慢后快 / 前快后死等」。";
    if (ttft >= 30000) {
      note = "首 token 已超过 30s 阈值：后端会 status 提示「模型响应较慢…可关联网 / 换模型 / 继续等待」。";
    } else if (web > 12000 && gen < 1500) {
      note = "联网偏长、生成偏短：检查 web_search_timeout 与搜索插件，避免「前慢后快」。";
    } else if (ret < 500 && ttft + gen > 20000) {
      note = "检索很快但生成很慢：属于慢模型场景，建议换模型或关联网减上下文。";
    }
    $("timingNote").textContent = note;
  }

  ["minScore", "bgRatio", "topicOk", "useRerank"].forEach(function (id) {
    $(id).addEventListener("input", renderGate);
    $(id).addEventListener("change", renderGate);
  });
  ["retMs", "webMs", "ttftMs", "genMs"].forEach(function (id) {
    $(id).addEventListener("input", renderTiming);
  });

  renderGate();
  renderTiming();
})();
