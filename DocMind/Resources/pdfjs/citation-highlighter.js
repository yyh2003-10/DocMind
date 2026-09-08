/**
 * DocMind 原著查证器核心引擎 (PDF.js + 容错模糊高亮定位)
 */
(function () {
  'use strict';

  // 配置 PDF.js worker
  if (window.pdfjsLib) {
    window.pdfjsLib.GlobalWorkerOptions.workerSrc = 'pdf.worker.min.js';
  }

  const container = document.getElementById('viewerContainer');
  const viewerNode = document.getElementById('viewer');
  const loadingOverlay = document.getElementById('loadingOverlay');
  const loadingText = document.getElementById('loadingText');
  const pageIndicator = document.getElementById('pageIndicator');
  const citationBadge = document.getElementById('citationBadge');
  const badgeText = document.getElementById('badgeText');

  const prevBtn = document.getElementById('prevBtn');
  const nextBtn = document.getElementById('nextBtn');
  const zoomInBtn = document.getElementById('zoomInBtn');
  const zoomOutBtn = document.getElementById('zoomOutBtn');
  const zoomFitBtn = document.getElementById('zoomFitBtn');

  let pdfLoadingTask = null;
  let pdfDocument = null;
  let pdfViewer = null;
  let pdfLinkService = null;
  let eventBus = null;

  let currentLoadedUrl = null;
  let pendingCitation = null; // { page: number, snippet: string }
  let currentHighlightedElements = [];

  // 初始化 PDF.js Viewer
  function initViewer() {
    if (!window.pdfjsViewer) {
      console.error('pdfjsViewer 未加载');
      return;
    }

    eventBus = new window.pdfjsViewer.EventBus();
    pdfLinkService = new window.pdfjsViewer.PDFLinkService({ eventBus });

    pdfViewer = new window.pdfjsViewer.PDFViewer({
      container: container,
      viewer: viewerNode,
      eventBus: eventBus,
      linkService: pdfLinkService,
      removePageBorders: true,
      textLayerMode: 1, // 开启 TextLayer 用于高亮和文字选择
    });

    pdfLinkService.setViewer(pdfViewer);

    // 页面渲染完成事件
    eventBus.on('pagesinit', function () {
      pdfViewer.currentScaleValue = 'page-width';
      updatePageIndicator();
      hideLoading();

      if (pendingCitation) {
        const cit = pendingCitation;
        pendingCitation = null;
        executeJumpAndHighlight(cit.page, cit.snippet);
      }
    });

    // 监听页码切换
    eventBus.on('pagechanging', function (evt) {
      updatePageIndicator();
      postMessageToWpf({ event: 'pageChanged', page: evt.pageNumber });
    });

    // 监听 TextLayer 渲染完成
    eventBus.on('textlayerrendered', function (evt) {
      const renderedPage = evt.pageNumber;
      if (pendingCitation && pendingCitation.page === renderedPage) {
        const cit = pendingCitation;
        pendingCitation = null;
        applyHighlightOnPage(renderedPage, cit.snippet);
      }
    });
  }

  function updatePageIndicator() {
    if (!pdfViewer || !pdfViewer.pagesCount) {
      pageIndicator.textContent = '- / -';
      return;
    }
    const cur = pdfViewer.currentPageNumber || 1;
    const total = pdfViewer.pagesCount;
    pageIndicator.textContent = `${cur} / ${total}`;
  }

  function showLoading(msg) {
    loadingText.textContent = msg || '正在载入文档...';
    loadingOverlay.classList.remove('hidden');
  }

  function hideLoading() {
    loadingOverlay.classList.add('hidden');
  }

  function showBadge(msg) {
    badgeText.textContent = msg;
    citationBadge.classList.add('visible');
    setTimeout(() => {
      citationBadge.classList.remove('visible');
    }, 4000);
  }

  // 绑定工具条事件
  prevBtn.addEventListener('click', () => {
    if (pdfViewer && pdfViewer.currentPageNumber > 1) {
      pdfViewer.currentPageNumber--;
    }
  });

  nextBtn.addEventListener('click', () => {
    if (pdfViewer && pdfViewer.currentPageNumber < pdfViewer.pagesCount) {
      pdfViewer.currentPageNumber++;
    }
  });

  zoomInBtn.addEventListener('click', () => {
    if (pdfViewer) {
      pdfViewer.currentScale = Math.min(3.0, (pdfViewer.currentScale || 1.0) * 1.15);
    }
  });

  zoomOutBtn.addEventListener('click', () => {
    if (pdfViewer) {
      pdfViewer.currentScale = Math.max(0.4, (pdfViewer.currentScale || 1.0) * 0.85);
    }
  });

  zoomFitBtn.addEventListener('click', () => {
    if (pdfViewer) {
      pdfViewer.currentScaleValue = 'page-width';
    }
  });

  /**
   * 加载 PDF 文件并跳转定位
   */
  async function loadPdfDocument(pdfUrl, targetPage, snippet) {
    if (!pdfViewer) {
      initViewer();
    }

    // 若当前已加载同一份文档，则不重复网络请求，直接翻页高亮
    if (currentLoadedUrl === pdfUrl && pdfDocument) {
      executeJumpAndHighlight(targetPage, snippet);
      return;
    }

    clearHighlights();
    currentLoadedUrl = pdfUrl;
    showLoading('正在载入原著 PDF...');

    if (pdfLoadingTask) {
      try {
        await pdfLoadingTask.destroy();
      } catch (e) { }
    }

    try {
      pdfLoadingTask = window.pdfjsLib.getDocument({
        url: pdfUrl,
        cMapUrl: 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/cmaps/',
        cMapPacked: true,
        enableXfa: false,
      });

      pdfDocument = await pdfLoadingTask.promise;
      pdfViewer.setDocument(pdfDocument);
      pdfLinkService.setDocument(pdfDocument, null);

      if (targetPage && targetPage > 0) {
        pendingCitation = { page: targetPage, snippet: snippet || '' };
      }

      postMessageToWpf({ event: 'pdfLoaded', numPages: pdfDocument.numPages, url: pdfUrl });
    } catch (err) {
      console.error('PDF 加载失败:', err);
      showLoading(`加载失败: ${err.message || '文件损坏或无法读取'}`);
      postMessageToWpf({ event: 'error', message: err.message });
    }
  }

  /**
   * 执行页面跳转与高亮
   */
  function executeJumpAndHighlight(page, snippet) {
    if (!pdfViewer || !pdfDocument) {
      pendingCitation = { page, snippet };
      return;
    }

    const pageNum = parseInt(page, 10);
    if (isNaN(pageNum) || pageNum < 1 || pageNum > pdfDocument.numPages) {
      return;
    }

    clearHighlights();

    // 1. 滚动页面到视口
    pdfViewer.scrollPageIntoView({ pageNumber: pageNum });

    // 2. 检查该页的 textLayer 是否已完成渲染
    const pageView = pdfViewer.getPageView(pageNum - 1);
    if (pageView && pageView.textLayer && pageView.textLayer.renderingDone) {
      applyHighlightOnPage(pageNum, snippet);
    } else {
      // 暂存等待 textlayerrendered 触发
      pendingCitation = { page: pageNum, snippet: snippet };
    }
  }

  /**
   * 清除之前的高亮
   */
  function clearHighlights() {
    currentHighlightedElements.forEach(el => {
      el.classList.remove('docmind-citation-highlight', 'docmind-citation-pulse');
    });
    currentHighlightedElements = [];
  }

  /**
   * 字符串归一化（用于容错模糊匹配：统一去换行、去空格、去标点差异）
   */
  function normalizeText(str) {
    if (!str) return '';
    return str
      .replace(/[\r\n\t\s]+/g, '') // 移除所有空白符和换行
      .replace(/[，,。\.、；;：:！!？?“”"''（）\(\)【】\[\]]/g, '') // 移除常见中英文标点
      .toLowerCase();
  }

  /**
   * 在指定页码的 TextLayer 中执行容错匹配并渲染黄色高亮
   */
  function applyHighlightOnPage(pageNum, snippet) {
    clearHighlights();

    const pageView = pdfViewer.getPageView(pageNum - 1);
    if (!pageView || !pageView.div) return;

    const textLayerDiv = pageView.div.querySelector('.textLayer');
    if (!textLayerDiv) return;

    const textSpans = Array.from(textLayerDiv.querySelectorAll('span'));
    if (textSpans.length === 0) return;

    if (!snippet || snippet.trim().length === 0) {
      showBadge(`已定位到第 P${pageNum} 页`);
      return;
    }

    // 建立 span 映射树
    // 每个 span 记录 normalizedOffset 范围
    let fullNormalized = '';
    const spanMap = []; // { span, normStart, normEnd }

    for (let i = 0; i < textSpans.length; i++) {
      const span = textSpans[i];
      const raw = span.textContent || '';
      const norm = normalizeText(raw);
      if (norm.length > 0) {
        const start = fullNormalized.length;
        fullNormalized += norm;
        const end = fullNormalized.length;
        spanMap.push({ span, normStart: start, normEnd: end });
      }
    }

    const normSnippet = normalizeText(snippet);
    if (normSnippet.length === 0 || fullNormalized.length === 0) {
      showBadge(`已定位到第 P${pageNum} 页`);
      return;
    }

    // 容错搜索：先尝试完整匹配；若失败，截取前 40 字符、中间 40 字符尝试匹配
    let matchIndex = fullNormalized.indexOf(normSnippet);
    let matchLen = normSnippet.length;

    if (matchIndex === -1 && normSnippet.length > 25) {
      // 尝试前 35 字符
      const prefix = normSnippet.substring(0, 35);
      matchIndex = fullNormalized.indexOf(prefix);
      matchLen = prefix.length;
    }

    if (matchIndex === -1 && normSnippet.length > 50) {
      // 尝试中间 30 字符
      const midStart = Math.floor((normSnippet.length - 30) / 2);
      const mid = normSnippet.substring(midStart, midStart + 30);
      matchIndex = fullNormalized.indexOf(mid);
      matchLen = mid.length;
    }

    if (matchIndex === -1 && normSnippet.length > 15) {
      // 降级尝试前 15 字符
      const shortPrefix = normSnippet.substring(0, 15);
      matchIndex = fullNormalized.indexOf(shortPrefix);
      matchLen = shortPrefix.length;
    }

    if (matchIndex !== -1) {
      const matchEnd = matchIndex + matchLen;
      // 找出覆盖在 [matchIndex, matchEnd] 区间的所有 span
      const matchedSpans = [];
      for (const item of spanMap) {
        // 判断区间是否有重叠
        if (item.normStart < matchEnd && item.normEnd > matchIndex) {
          matchedSpans.push(item.span);
        }
      }

      if (matchedSpans.length > 0) {
        matchedSpans.forEach(span => {
          span.classList.add('docmind-citation-highlight', 'docmind-citation-pulse');
          currentHighlightedElements.push(span);
        });

        // 平滑滚动居中聚焦第一个匹配项
        setTimeout(() => {
          matchedSpans[0].scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
        }, 100);

        showBadge(`已高亮切片依据 (第 P${pageNum} 页)`);
        postMessageToWpf({ event: 'citationLocated', success: true, page: pageNum, count: matchedSpans.length });
        return;
      }
    }

    // 若未在当前页文字层搜到具体字符（例如 OCR 扫描件或排版特殊），至少平滑滚动到该页面顶部
    showBadge(`已跳转至第 P${pageNum} 页`);
    postMessageToWpf({ event: 'citationLocated', success: false, page: pageNum });
  }

  // 与 WPF 通信协议
  function postMessageToWpf(data) {
    if (window.chrome && window.chrome.webview) {
      try {
        window.chrome.webview.postMessage(JSON.stringify(data));
      } catch (e) {
        console.warn('向 WPF 发送消息失败:', e);
      }
    }
  }

  // 监听来自 WPF 的调用
  if (window.chrome && window.chrome.webview) {
    window.chrome.webview.addEventListener('message', function (event) {
      try {
        const msg = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
        handleWpfMessage(msg);
      } catch (e) {
        console.error('解析 WPF 指令失败:', e);
      }
    });
  }

  function handleWpfMessage(msg) {
    if (!msg || !msg.action) return;

    switch (msg.action) {
      case 'loadPdf':
        loadPdfDocument(msg.url, msg.page, msg.snippet);
        break;
      case 'jumpTo':
        executeJumpAndHighlight(msg.page, msg.snippet);
        break;
      case 'clearHighlight':
        clearHighlights();
        break;
      case 'setTheme':
        if (msg.isDark) {
          document.body.classList.add('dark');
        } else {
          document.body.classList.remove('dark');
        }
        break;
      case 'setZoom':
        if (pdfViewer && msg.zoom) {
          pdfViewer.currentScaleValue = msg.zoom;
        }
        break;
    }
  }

  // 暴露调试全局对象
  window.__docmindPdfViewer = {
    loadPdf: loadPdfDocument,
    jumpTo: executeJumpAndHighlight,
    clearHighlights: clearHighlights,
  };

  // 启动初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initViewer);
  } else {
    initViewer();
  }
})();
