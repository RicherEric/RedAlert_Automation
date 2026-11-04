// 期中報告（5 分鐘）：照原本論文導讀的順序講 IEEE 論文（原文圖表照搬），最後才講怎麼用到紅色警戒 2，
// 附 1 分鐘示範影片。版面刻意簡單。
// cd thesis && npm install && node build_midterm.js 期中報告_5分鐘.pptx
// 主題色靠 Claude Code pptx skill 的 apply_theme.js：設 APPLY_THEME 指到它，或讓下面自動在 ~/.claude/skills 裡找。
const pptxgen = require("pptxgenjs");
const fs = require("fs");
const os = require("os");
const path = require("path");

const findApplyTheme = () => {
  if (process.env.APPLY_THEME) return process.env.APPLY_THEME;
  const stack = [path.join(os.homedir(), ".claude", "skills")];
  while (stack.length) {
    const dir = stack.pop();
    let entries = [];
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { continue; }
    for (const e of entries) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) stack.push(p);
      else if (e.name === "apply_theme.js" && p.includes(path.join("pptx", "scripts"))) return p;
    }
  }
  throw new Error("找不到 pptx skill 的 apply_theme.js；請設環境變數 APPLY_THEME 指到它");
};
const { applyTheme } = require(findApplyTheme());

const OUT = process.argv[2] || "midterm.pptx";
const ROOT = path.resolve(__dirname, "..");
const IMG = (n) => path.join(ROOT, "thesis/images", n);
const V = path.join(ROOT, "thesis/videos") + "/";

const THEME = {
  name: "Midterm",
  headFontFace: "Microsoft JhengHei",
  bodyFontFace: "Microsoft JhengHei",
  colors: {
    dk1: "1F2937", lt1: "FFFFFF", dk2: "4B5563", lt2: "F3F4F6",
    accent1: "1D4ED8", accent2: "B91C1C", accent3: "9CA3AF", accent4: "D97706",
    accent5: "047857", accent6: "6B7280", hlink: "1D4ED8", folHlink: "6B7280",
  },
};

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9"; // 10 x 5.625 in
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "期中報告：資料驅動的建造順序最佳化";
const C = pres.SchemeColor;

pres.defineSlideMaster({
  title: "TITLE",
  background: { color: THEME.colors.lt1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.7, y: 1.0, w: 5.6, h: 1.5, fontSize: 28, bold: true, color: C.text1, valign: "bottom", align: "left", margin: 0 }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.7, y: 2.75, w: 5.6, h: 2.0, fontSize: 13, color: C.text2, valign: "top", align: "left", margin: 0 }, text: "" } },
  ],
});
pres.defineSlideMaster({
  title: "CONTENT",
  background: { color: THEME.colors.lt1 },
  margin: [0.5, 0.5, 0.5, 0.5],
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.3, w: 8.8, h: 0.7, fontSize: 24, bold: true, color: C.text1, valign: "middle", align: "left", margin: 0 }, text: "" } },
    { line: { x: 0.6, y: 1.05, w: 8.8, h: 0, line: { color: C.accent1, width: 1.5 } } },
  ],
  slideNumber: { x: 9.0, y: 5.2, w: 0.5, h: 0.25, fontSize: 9, color: C.accent3, align: "right" },
});

const slide = (section, title) => {
  const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: section });
  s.addText(title, { placeholder: "title" });
  return s;
};
const text = (s, t, o) => s.addText(t, { isTextBox: true, margin: 0, color: C.text1, fontSize: 15, valign: "top", ...o });
// 條列：字串是一般項目，[粗體, 說明] 是「標籤：說明」
const bullets = (s, items, o) => s.addText(items.flatMap((it, i) => {
  const last = i === items.length - 1;
  const opts = { bullet: true, paraSpaceAfter: 8 };
  if (typeof it === "string") return [{ text: it, options: { ...opts, breakLine: !last } }];
  return [{ text: it[0], options: { ...opts, bold: true } }, { text: it[1], options: { breakLine: !last } }];
}), { isTextBox: true, margin: 0, color: C.text1, fontSize: 14, valign: "top", ...o });
const image = (s, file, x, y, w, h, alt) => s.addImage({ path: IMG(file), x, y, w, h, altText: alt, objectName: file });
const source = (s, t) => text(s, t, { x: 0.6, y: 5.2, w: 8, h: 0.25, fontSize: 9, color: C.accent3 });

// 流程圖：左半是跑演算法前用資料算好的東西，右半是每一代的演化迴圈。論文版和紅警版共用這個版型。
const flowchart = (s, f) => {
  const box = (t, x, y, w, h, o = {}) => s.addText(t, {
    shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.08, x, y, w, h, margin: 0.06,
    fill: { color: C.background1 }, line: { color: C.accent1, width: 1.25 },
    fontSize: 12, color: C.text1, align: "center", valign: "middle", ...o,
  });
  const arrow = (x, y, w, h, o = {}) => s.addShape(pres.shapes.LINE, {
    x, y, w, h, line: { color: C.text2, width: 1.5, endArrowType: "triangle" }, ...o,
  });
  const two = (head, sub, subColor = C.text1) => [
    { text: head, options: { bold: true, breakLine: true } }, { text: sub, options: { fontSize: 9.5, color: subColor } }];
  // left lane: prepared from the data before the search runs
  s.addShape(pres.shapes.RECTANGLE, { x: 0.45, y: 1.2, w: 5.15, h: 3.15, fill: { color: C.background2 }, line: { type: "none" }, objectName: "prep lane" });
  text(s, f.leftTitle, { x: 0.55, y: 1.25, w: 4.9, h: 0.3, fontSize: 11, bold: true, color: C.text2 });
  box(f.data, ...f.dataBox, { objectName: "data box" });
  box(two(...f.topk), 3.0, 1.65, 2.45, 0.85, { objectName: "topk box" });
  box(two(...f.xi), 3.0, 3.1, 2.45, 0.85, { objectName: "xi box" });
  f.dataArrows.forEach(([x, y, w, h, flipV], i) => arrow(x, y, w, h, { flipV, objectName: `data arrow ${i + 1}` }));
  text(s, f.xiNote, { x: 0.55, y: 4.0, w: 4.9, h: 0.3, fontSize: 11, color: C.text2 });
  // right lane: one pass per generation; f.hot is the step xi feeds into
  text(s, f.rightTitle, { x: 5.95, y: 1.25, w: 3.5, h: 0.3, fontSize: 11, bold: true, color: C.text2 });
  let y = 1.6;
  const mids = f.steps.map(([head, sub, subColor], i) => {
    const h = sub ? 0.65 : 0.5;
    box(sub ? two(head, sub, subColor || C.accent1) : head, 5.95, y, 3.3, h, sub
      ? { fill: { color: C.background2 }, line: { color: C.accent1, width: 2.25 }, objectName: `step ${i + 1}` }
      : { objectName: `step ${i + 1}` });
    if (i < f.steps.length - 1) arrow(7.6, y + h, 0, 0.2, { objectName: `step arrow ${i + 1}` });
    const mid = y + h / 2;
    y += h + 0.2;
    return mid;
  });
  // loop back from the last step to the first, along the right edge
  const [top, bottom] = [mids[0], mids[mids.length - 1]];
  s.addShape(pres.shapes.LINE, { x: 9.25, y: bottom, w: 0.25, h: 0, line: { color: C.text2, width: 1.5 }, objectName: "loop 1" });
  s.addShape(pres.shapes.LINE, { x: 9.5, y: top, w: 0, h: bottom - top, line: { color: C.text2, width: 1.5 }, objectName: "loop 2" });
  arrow(9.25, top, 0.25, 0, { flipH: true, objectName: "loop 3" });
  text(s, "下一代", { x: 9.55, y: (top + bottom) / 2 - 0.25, w: 0.4, h: 0.5, fontSize: 9, color: C.text2 });
  // inputs from the left lane: TopK seeds the population, xi feeds the hot step
  arrow(5.45, 1.98, 0.5, 0, { objectName: "topk to population" });
  const xiY = 3.52, target = mids[f.hot];
  arrow(5.45, Math.min(xiY, target), 0.5, Math.abs(xiY - target), { flipV: target < xiY, objectName: "xi to step" });
  text(s, f.output, { x: 5.95, y: y - 0.12, w: 3.3, h: 0.3, fontSize: 11, bold: true, color: C.accent1, align: "center" });
};
// 編號步驟：圈號 + 粗體小標 + 灰字說明，讓一頁的重點照講的順序排
const step = (s, x, w, n, head, body, y, h, hot = false) => {
  text(s, n, { x, y, w: 0.4, h: 0.4, fontSize: 17, bold: true, color: hot ? C.accent4 : C.accent1 });
  text(s, head, { x: x + 0.42, y: y + 0.03, w: w - 0.42, h: 0.35, fontSize: 13.5, bold: true, color: hot ? C.accent4 : C.text1 });
  text(s, body, { x: x + 0.42, y: y + 0.38, w: w - 0.42, h, fontSize: 11.5, color: C.text2 });
};
// 基因演算法的三個弱點各補一招，架構圖和第 8 頁共用
const FIXES = [["硬限制下分數沒高低", "懲罰函數"], ["卡在小山頭", "模擬退火"], ["起點太差", "高手開局 TopK"]];
const fixChip = (s, x, y, w, [weak, fix]) => s.addText([
  { text: weak + " → ", options: { color: C.text2 } }, { text: fix, options: { bold: true, color: C.accent1 } },
], { shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.06, x, y, w, h: 0.32, margin: 0.03, fill: { color: C.background1 },
  line: { color: C.accent1, width: 0.75 }, fontSize: 10, align: "center", valign: "middle", objectName: "fix chip" });
// 對應到流程圖哪一格的小標籤，放在標題列右側
const mapTag = (s, t) => s.addText(t, {
  shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.06, x: 7.75, y: 0.45, w: 1.65, h: 0.36, margin: 0.03,
  fill: { color: C.background2 }, line: { color: C.accent1, width: 1 }, fontSize: 10, color: C.accent1, bold: true,
  align: "center", valign: "middle", objectName: "map tag",
});

// ================================================================ 1. title
pres.addSection({ title: "開場" });
{
  const s = pres.addSlide({ masterName: "TITLE", sectionTitle: "開場" });
  s.addText("建造順序最佳化", { placeholder: "title" });
  s.addText([
    { text: "期中報告", options: { breakLine: true, bold: true } },
    { text: "報告人：M1527488 柯義詮", options: { breakLine: true } },
    { text: " ", options: { breakLine: true, fontSize: 8 } },
    { text: "A Data-driven Approach to Solve a Production Constrained Build-order Optimization Problem", options: { breakLine: true } },
    { text: "Wang, Zeng, Chen, Cao｜CCC 2019（IEEE）" },
  ], { placeholder: "body" });
  s.addImage({ path: IMG("StarCraft_box_art.jpg"), x: 7.07, y: 1.3, w: 2.0, h: 2.52, altText: "星海爭霸遊戲封面", objectName: "starcraft cover" });
  text(s, "論文用的遊戲：星海爭霸", { x: 6.6, y: 3.9, w: 2.93, h: 0.3, fontSize: 10, color: C.accent3, align: "center" });
  text(s, "封面圖片來源：Wikipedia（合理使用）", { x: 6.6, y: 5.2, w: 2.93, h: 0.25, fontSize: 8, color: C.accent3, align: "center" });
  s.addNotes("（約 10 秒）大家好，我報告的是 2019 年中國控制會議、收錄在 IEEE 的這篇論文：用職業選手的錄影，幫基因演算法排星海爭霸的建造順序。這篇論文其實只回答兩個問題：兵要出多少、從哪裡開始找，答案都是看資料。");
}

// ================================================================ 2. background
pres.addSection({ title: "論文內容" });
{
  const s = slide("論文內容", "什麼是 Build-order？");
  image(s, "Zerg_colony_(StarCraft).png", 0.6, 1.35, 4.0, 2.99, "星海爭霸遊戲畫面");
  text(s, "星海爭霸遊戲畫面（Wikipedia，合理使用）", { x: 0.6, y: 4.42, w: 4.0, h: 0.25, fontSize: 9, color: C.accent3 });
  text(s, "建造順序就像煮菜步驟：沒有兵營就不能出陸戰隊。", { x: 5.0, y: 1.35, w: 4.4, h: 0.7, fontSize: 15, bold: true });
  bullets(s, [
    ["人口：", "人口不夠就不能出新兵"],
    ["科技樹：", "先有兵營才能蓋工廠"],
    ["資源：", "礦物和瓦斯"],
    ["時間：", "每個建築、單位都有建造時間"],
  ], { x: 5.0, y: 2.2, w: 4.4, h: 1.9 });
  text(s, "目標：在這些限制下，開局每個時段拿到最多的兵。", { x: 5.0, y: 4.1, w: 4.4, h: 0.35, fontSize: 14, color: C.accent1, bold: true });
  s.addNotes("（約 25 秒）Build-order 就是建造順序，可以想成煮菜步驟：沒有兵營就不能出陸戰隊。遊戲本身有四種限制：人口、科技樹、資源、時間。最佳化的目標，就是在這些限制下，開局前 12 分鐘的每個時段都拿到最多的兵。這些規則都知道，唯一不知道的是：兵到底要出多少才夠？");
}

// ================================================================ 3. pain point: what is xi
{
  const s = slide("論文內容", "到底要出多少兵？");
  bullets(s, [
    ["式 (1)：", "4 個時段 × 6 種兵 = 24 格，每格的兵數全部加總，越多越好"],
    ["式 (2)：", "遊戲規則，例：沒有兵營就不能出陸戰隊、人口滿了不能再出兵"],
    ["式 (3)：", "每格至少 ξ 個，例：第 4 時段（9–12 分）攻城坦克至少幾台？5 台？20 台？"],
    ["難點：", "ξ 跟對手有關，但戰爭迷霧看不到對手"],
    ["以前：", "ξ 由專家憑經驗猜一個數字，實戰常不如預期"],
  ], { x: 0.6, y: 1.3, w: 4.5, h: 3.7, fontSize: 13 });
  image(s, "paper_eq123.png", 5.3, 1.35, 4.1, 2.69, "論文式 (1)(2)(3)：目標函數與限制條件");
  source(s, "右圖：原文第 3.2 節，式 (1)–(3)");
  s.addNotes("（約 40 秒）用論文的設定舉例：開局 12 分鐘切成 4 個時段、6 種兵。式一的兩個 Σ 看起來嚇人，其實就是加總：想像一張 4 列 6 欄的表，一列是一個時段，一欄是一種兵，每一格寫這個時段這種兵有幾隻；兩個 Σ 就是把這 24 格全部加起來，max 就是越多越好。式二的 GameConstraints 是遊戲規則的代號，例如沒有兵營就不能出陸戰隊、人口滿了就不能再出兵；式三是門檻，也就是生存底線：每一格至少 ξ 個。舉例來說，第 4 個時段，也就是第 9 到 12 分鐘，攻城坦克至少要幾台？5 台還是 20 台？問題是 ξ 要設多少？它跟對手有關，可是有戰爭迷霧看不到。以前都是專家憑經驗猜一個數字，所以算出來的最佳解實戰常常不好用。作者的做法是：不用猜，看資料。");
}

// ================================================================ 4. the map: the whole paper in one diagram
{
  const s = slide("論文內容", "整體架構：資料定題目，演算法找答案");
  flowchart(s, {
    leftTitle: "資料定題目：跑演算法之前就算好",
    data: [{ text: "459 場", options: { bold: true, breakLine: true } }, { text: "職業錄影" }],
    dataBox: [0.65, 2.3, 1.6, 0.95],
    dataArrows: [[2.25, 2.08, 0.75, 0.45, true], [2.25, 3.0, 0.75, 0.5, false]],
    topk: ["② 起點（TopK）", "高手開局評分後取前 K 個，其餘隨機"],
    xi: ["① 門檻 ξ（表 1）", "依總兵量排序取第 N·p 名（p = 0.5）"],
    xiNote: "ξ 算一次就固定，之後不再改",
    rightTitle: "演算法找答案：每一代都跑一次",
    steps: [["族群：一群建造順序（最長 200 步）"], ["模擬器跑一遍，數出每格兵量 Unit"],
      ["評分：Σ ( Unit + δ(Unit − ξ) )"], ["基因演算法＋模擬退火 → 下一代"]],
    hot: 2,
    output: "跑完 153 代 → 最佳建造順序",
  });
  text(s, "基因演算法的三個弱點，論文各補一招：", { x: 0.45, y: 4.8, w: 2.55, h: 0.32, fontSize: 10, bold: true, color: C.text2, valign: "middle" });
  [[3.0, 2.45], [5.53, 1.8], [7.41, 2.0]].forEach(([x, w], i) => fixChip(s, x, 4.8, w, FIXES[i]));
  source(s, "依原文第 3、4 節整理的流程圖；高手開局也是用同一個評分（式 5）排序");
  s.addNotes("（約 40 秒）講細節之前，先看整篇的架構，後面每一頁都對應到這張圖的一格。左邊是資料定題目：用錄影算好 ① 門檻 ξ 和 ② 起點 TopK，ξ 算一次就固定。注意 ξ 是及格線，不是答案。右邊是演算法找答案：要找的是一條建造順序，最長 200 步，一步就是蓋一個東西，例如先蓋工兵、再蓋補給站、兵營、然後出兵；演算法要排出怎麼蓋，才能每一格都過及格線，而且兵越多越好。每一代把建造順序丟進模擬器、評分，再用基因演算法加模擬退火產生下一代，ξ 就用在評分這一格。下面一排是基因演算法的三個弱點，論文各補一招，後面會一一講到。接下來就照這張圖，先講 ①，再講評分，最後講 ②。");
}

// ================================================================ 5. Q1 answer: learn xi from replays
{
  const s = slide("論文內容", "ξ → 怎麼從錄影學出來");
  mapTag(s, "地圖：① 門檻 ξ");
  bullets(s, [
    "每場錄影 → 一個「4 時段 × 6 兵種」的敵方兵量向量（每 12 幀記一次）",
    "459 場依總兵量由少到多排（第 1 名兵最少），取第 N·p 名當 ξ",
    "式 (6)：我的兵量要至少贏過 p 比例的對手",
  ], { x: 0.6, y: 1.25, w: 4.6, h: 1.45, fontSize: 13 });
  image(s, "paper_eq6.png", 0.6, 2.75, 4.6, 0.46, "論文式 (6)：觀察到的敵方兵量不超過我方產量的機率至少為 p");
  // p 的例子：N = 459
  const hdr = (t) => ({ text: t, options: { bold: true, color: C.background1, fill: { color: C.accent1 }, fontSize: 11, valign: "middle" } });
  const cell = (t, o = {}) => ({ text: t, options: { color: C.text1, fontSize: 11, valign: "middle", ...o } });
  s.addTable([
    [hdr("p"), hdr("兵量多過幾場"), hdr("意思")],
    [cell("0.5", { bold: true }), cell("約 230 場"), cell("贏過一半的對手（論文用的）")],
    [cell("0.8", { bold: true }), cell("約 367 場"), cell("那場兵更多 → ξ 更大")],
  ], {
    x: 5.4, y: 1.3, w: 4.0, colW: [0.45, 1.35, 2.2], rowH: [0.32, 0.36, 0.36],
    border: { type: "solid", pt: 0.75, color: "D1D5DB" }, margin: 0.05, objectName: "p example table",
  });
  text(s, "p 越高越保守：防得住兵多的對手，但門檻高、資源被綁住。論文只做 p = 0.5，對勝負的影響沒有實驗。", { x: 5.4, y: 2.5, w: 4.0, h: 0.65, fontSize: 11, color: C.text2 });
  image(s, "paper_table1.png", 1.7, 3.3, 6.6, 1.54, "論文表 1：p = 0.5 時，四個時段六種單位的門檻值");
  source(s, "圖：原文第 3.3 節式 (6)、表 1（p = 0.5；同名次取平均，所以有 0.57 這種小數）");
  s.addNotes("（約 45 秒）先看地圖上的 ①，這就是第一個問題的答案，也是整篇最有價值的地方。作者把 459 場職業錄影跑一遍，每 12 幀記下看到的敵方單位，每場整理成一個 4 時段乘 6 兵種的向量。接著把 459 場依總兵量由少到多排名，第 1 名是兵最少的那場，越後面兵越多，取第 N 乘 p 名當 ξ。式六說的是：我的兵量要至少贏過 p 比例的對手。p 等於 0.5，就是取第 230 名左右，也就是兵量多過約 230 場的那場，正中間，等於贏過一半的對手，論文用的就是這個；那場第 4 時段有幾台攻城坦克，坦克的 ξ 就是幾台。如果 p 等於 0.8，就要取第 367 名左右，那場對手的兵量多過約 367 場，ξ 就更大，等於要準備對付比八成對手都多的兵，所以說比較保守：防得住兵多的對手，但門檻變高，資源被綁在這些兵上，達不到就被扣分。不過論文只做了 p 等於 0.5，p 調高對勝負有什麼影響，論文沒有實驗。下面的表一就是 p 等於 0.5 學出來的門檻，同名次的取平均，所以有 0.57 這種小數。這張表每個數字代表什麼，下一頁來看。");
}

// ================================================================ 6. table 1 in plain words: StarCraft units
{
  const s = slide("論文內容", "學出來的門檻：人族內戰的 6 種兵");
  mapTag(s, "地圖：① 門檻 ξ");
  const hdr = (t) => ({ text: t, options: { bold: true, color: C.background1, fill: { color: C.accent1 }, fontSize: 11, valign: "middle", align: "center" } });
  const cell = (t, o = {}) => ({ text: t, options: { color: C.text1, fontSize: 12, valign: "middle", ...o } });
  // same numbers as paper Table 1, transposed: one row per unit, one column per time period
  const units = [
    ["陸戰隊", "Marine", "基本步兵", ["0.57", "2.65", "2.83", "2.83"]],
    ["禿鷹", "Vulture", "快速機車，騷擾、埋雷", ["0", "1.52", "1.83", "2.78"]],
    ["歌利亞", "Goliath", "機甲，主要防空", ["0", "0.26", "2.52", "6.57"]],
    ["攻城坦克", "Siege Tank", "射程超遠，內戰主力", ["0", "1.17", "4.00", "12.43"]],
    ["幽靈戰機", "Wraith", "會隱形的戰機", ["0", "0.43", "1.65", "2.26"]],
    ["運輸機", "Dropship", "空投", ["0", "0", "0.30", "1.39"]],
  ];
  const rows = [[hdr("單位"), hdr("用途"), hdr("第 1 時段\n0–3 分鐘"), hdr("第 2 時段\n3–6 分鐘"), hdr("第 3 時段\n6–9 分鐘"), hdr("第 4 時段\n9–12 分鐘")]].concat(
    units.map(([zh, en, role, xi]) => [
      cell([{ text: zh + " ", options: { bold: true } }, { text: en, options: { fontSize: 9, color: C.text2 } }]),
      cell(role, { fontSize: 11 }),
      ...xi.map((v, i) => cell(v, { align: "center", bold: i === 3, ...(v === "12.43" ? { fill: { color: C.background2 }, color: C.accent1 } : {}) })),
    ]));
  s.addTable(rows, {
    x: 0.6, y: 1.2, w: 8.8, colW: [2.0, 2.4, 1.1, 1.1, 1.1, 1.1], rowH: [0.5, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],
    border: { type: "solid", pt: 0.75, color: "D1D5DB" }, margin: 0.06, objectName: "starcraft units table",
  });
  text(s, "這是 p = 0.5 學出來的門檻。表上不是分數，是「對手這種兵至少有幾隻」：459 場依總兵量排好，取中間那場（第 230 名），看它每個時段各有幾隻；同名次有好幾場就平均，所以有小數。", { x: 0.6, y: 4.15, w: 8.8, h: 0.5, fontSize: 11, color: C.text2 });
  text(s, "攻城坦克從 0 漲到 12.43 台，漲最多：人族內戰中期就是坦克大戰。", { x: 0.6, y: 4.7, w: 8.8, h: 0.35, fontSize: 13, bold: true, color: C.accent1 });
  source(s, "數字：原文表 1（p = 0.5），轉成一列一種兵；12 種兵裡全為 0 的已剔除，剩這 6 種；求解時四捨五入成整數");
  s.addNotes("（約 30 秒）學出來的門檻到底長什麼樣？先講清楚，表上的數字不是分數，是對手這種兵至少有幾隻。它跟上一頁 p 等於 0.5 的表一是同一組數字，只是我把它轉成一列一種兵、加上中文。怎麼來的：459 場依總兵量排好，取中間那場，看它每個時段各有幾隻；同名次有好幾場就取平均，所以有小數。論文用的是星海爭霸人族打人族，最後留下 6 種兵：陸戰隊是基本步兵，禿鷹是快速機車，歌利亞主要防空，攻城坦克射程很遠、是內戰主力，幽靈戰機會隱形，運輸機負責空投。看最右邊第 4 時段：攻城坦克從 0 一路漲到 12.43 台，可以看出中期就是坦克大戰。有了門檻，下一步是怎麼放進演算法。");
}

// ================================================================ 7. penalty: putting xi into the objective
{
  const s = slide("論文內容", "懲罰函數：沒達標就扣大分");
  mapTag(s, "地圖：評分");
  image(s, "paper_eq45.png", 0.6, 1.25, 4.6, 2.0, "論文式 (4)(5)：懲罰函數，未達門檻時額外扣 N");
  // 小例子：9–12 分鐘攻城坦克門檻 ξ = 12（取整數方便講）
  const hdr = (t) => ({ text: t, options: { bold: true, color: C.background1, fill: { color: C.accent1 }, fontSize: 11, align: "center", valign: "middle" } });
  const cell = (t, o = {}) => ({ text: t, options: { color: C.text1, fontSize: 11, align: "center", valign: "middle", ...o } });
  s.addTable([
    [hdr("攻城坦克（門檻 12）"), hdr("硬限制"), hdr("懲罰 δ（式 4）")],
    [cell("14 台"), cell("合格"), cell("+2")],
    [cell("11 台"), cell("淘汰", { color: C.accent2 }), cell("−1 − N", { bold: true })],
    [cell("2 台"), cell("淘汰", { color: C.accent2 }), cell("−10 − N", { bold: true })],
  ], {
    x: 0.6, y: 3.45, w: 4.6, colW: [1.9, 1.2, 1.5], rowH: [0.34, 0.32, 0.32, 0.32],
    border: { type: "solid", pt: 0.75, color: "D1D5DB" }, margin: 0.04, objectName: "penalty example table",
  });
  // problem -> fix -> effect, in the same order as the notes
  step(s, 5.5, 3.9, "①", "問題：基因演算法靠分數挑方案", "每一代要比分數、挑好的繁殖。門檻若當硬限制，不合格就淘汰，差 1 台和差 10 台一樣爛，演算法不知道往哪改。像考試 59 分和交白卷都算 0 分。", 1.25, 0.95);
  step(s, 5.5, 3.9, "②", "作法：改成扣分（式 4）", [
    { text: "達標：超過幾台，加幾分", options: { breakLine: true } },
    { text: "沒達標：差幾台扣幾分，再多扣一個大的 N" },
  ], 2.7, 0.55);
  step(s, 5.5, 3.9, "③", "效果：評分有坡度", "N 讓不合格的一定輸給合格的；差額讓「差一點」比「差很多」高分（看左下）。式 5 把這規則套到 24 格再加總。", 3.75, 0.95);
  source(s, "左圖：原文第 3.2 節，定義 1、式 (4)–(5)；左下表為說明用的例子");
  s.addNotes("（約 35 秒）回到地圖右邊的評分格。基因演算法每一代是靠分數挑出比較好的方案去繁殖；門檻如果當成硬限制，就是斷崖式評分：不合格的方案直接淘汰，看不出誰比較接近，就像考試 59 分和交白卷都算 0 分。所以作者用懲罰函數，把硬限制變成軟限制。看式四：x 是我蓋了幾台，ω 是門檻。蓋夠了，超過幾台就加幾分；不夠的話，差幾台扣幾分，再多扣一個很大的 N。看左下的例子：門檻 12 台，蓋 11 台和蓋 2 台都會被扣 N，所以一定輸給合格的方案；但一個只多扣 1，一個多扣 10，演算法就知道 11 台那個比較接近，往那個方向進化。式五就是把這個規則套到 24 格上再加總。到這裡，題目就列好了。");
}

// ================================================================ 8. Q2 answer: GA + pro openings (TopK)
{
  const s = slide("論文內容", "怎麼求解，實驗怎麼設計");
  mapTag(s, "地圖：② 起點 TopK");
  text(s, "改良版基因演算法：五個步驟", { x: 0.6, y: 1.25, w: 4.4, h: 0.35, fontSize: 15, bold: true, color: C.accent1 });
  [
    ["①", "為什麼要搜尋", "每步都有很多東西可選，200 步的組合是天文數字"],
    ["②", "解長什麼樣", "一條染色體 = 一個建造順序，一步 = 蓋一個東西"],
    ["③", "怎麼評分", "自製簡化模擬器跑一遍，用式 5 打分"],
    ["④", "怎麼改良", "交配、突變；加模擬退火，前期敢接受較差的解"],
    ["⑤", "從哪開始：高手開局 TopK", "錄影裡實際打過的建造順序，評分後取前 K 個"],
  ].forEach(([n, head, body], i) => step(s, 0.6, 4.7, n, head, body, 1.68 + i * 0.68, 0.3, i === 4));
  // right: the one-line takeaway first, the experiment settings under it
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 5.6, y: 1.25, w: 3.8, h: 1.95, rectRadius: 0.08, fill: { color: C.background2 }, line: { type: "none" }, objectName: "summary box" });
  text(s, "基因演算法適合「只能打分數」的題目，但有三個弱點，論文各補一招", { x: 5.75, y: 1.35, w: 3.5, h: 0.55, fontSize: 12, bold: true, color: C.text1 });
  FIXES.forEach((f, i) => fixChip(s, 5.75, 1.98 + i * 0.4, 3.5, f));
  text(s, "實驗設定", { x: 5.6, y: 3.4, w: 3.8, h: 0.35, fontSize: 14, bold: true, color: C.accent1 });
  bullets(s, [
    "459 場人族內戰錄影（從 7659 場挑出）",
    "開局 12 分鐘 = 4 個時段 × 6 種兵",
    "族群 150 / 200，每組參數跑 30 次",
    "對照組：初始族群全部隨機",
  ], { x: 5.6, y: 3.78, w: 3.8, h: 1.35, fontSize: 11.5 });
  source(s, "原文第 4、5 節；153 代是依溫度參數推算。模擬退火具體怎麼接進基因演算法，論文沒有寫，只引用文獻 [20]");
  s.addNotes("（約 75 秒）題目列好了，接著是怎麼解。先講整體：建造順序本身就是一串序列，可以直接當染色體，而評分只能靠模擬器跑出來，基因演算法只需要每個方案一個分數就能運作，所以很適合；但它有三個弱點，論文各補一招，右上角這三個框：門檻當硬限制時分數沒有高低、不合格的全部同分，用懲罰函數；容易卡在小山頭用模擬退火，隨機起點太差用高手開局。接著照左邊五步講。第一，為什麼要搜尋？建造順序最長 200 步，一步就是蓋一個東西，每一步都有工兵、建築、各種兵可以選，排列組合是天文數字，不能窮舉。第二，一條染色體就是一個建造順序。第三，每個建造順序丟進作者自己寫的簡化模擬器跑一遍，用剛剛的式五打分數。第四，用基因演算法交配、突變產生下一代，再加上模擬退火：單純的基因演算法只留變好的，容易卡在小山頭；模擬退火前期溫度高，比較差的解也有機會被接受，敢跳出去找，溫度每代乘 0.9 慢慢降，後期就只留變好的。論文說實際跑了 153 代，就是溫度從 1000 降到 0.0001 剛好要 153 代，這是我從參數算出來的；不過模擬退火具體怎麼接進基因演算法，論文沒有寫，只引用了一篇文獻。第五，也就是地圖上的 ②：從全部隨機開始找太慢，所以作者把高手錄影裡的建造順序排序後，取前 K 個放進初始族群，這是資料第二次出場，也是第二個問題的答案。注意這裡不是請高手出意見，而是拿錄影裡實際打過的建造順序，先用模擬器評分再挑分數最高的；放進去以後，基因演算法還會繼續改良。實驗用 459 場人族內戰，每組參數跑 30 次，跟全部隨機的初始族群比較。");
}

// ================================================================ 9. fig 1 + fig 2
{
  const s = slide("論文內容", "結果：加入高手開局，起跑點直接贏一大截");
  mapTag(s, "地圖：跑完的結果");
  // figure 2 carries the message, so it gets the full width; figure 1 sits small at the bottom left
  image(s, "paper_fig2.png", 0.6, 1.2, 8.8, 2.29, "論文圖 2：使用不同數量玩家建造順序時的平均適應度，(a)(b)(c) 為染色體長度 100、150、200");
  image(s, "paper_fig1.png", 0.6, 3.6, 1.85, 1.51, "論文圖 1：不使用玩家建造順序時，平均適應度隨世代變化");
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 2.6, y: 3.6, w: 6.8, h: 0.78, rectRadius: 0.08, fill: { color: C.background2 }, line: { type: "none" }, objectName: "how to read" });
  text(s, [
    { text: "怎麼看：", options: { bold: true, color: C.accent1 } },
    { text: "橫軸 = 第幾代（0～153）；縱軸 = 每一代最好方案的分數（30 次平均），越高越好。", options: { breakLine: true } },
    { text: "一條線 = 一組設定（L 長度、P 族群數、TopK 放幾個高手開局）；線往上爬 = 一代代找到更好的建造順序。", options: { breakLine: true } },
    { text: "左下圖 1：全部隨機的 6 組設定，長度 200 跑完 153 代還在爬。" },
  ], { x: 2.72, y: 3.63, w: 6.6, h: 0.72, fontSize: 10, color: C.text1 });
  bullets(s, [
    "圖 2 藍線 = 全部隨機（TopK0）：從 −1000 以下慢慢爬，長度越長落後越多",
    "其他線 = 放 1～200 個高手開局：第 0 代就在 −100～−300，很快到 200 以上",
  ], { x: 2.6, y: 4.45, w: 6.8, h: 0.7, fontSize: 11.5 });
  source(s, "圖片來源：原文圖 1、圖 2");
  s.addNotes("（約 45 秒）效果如何？先說怎麼看這兩張圖：橫軸是第幾代，縱軸是每一代最好方案的分數，取 30 次平均，越高越好；一條線就是一組設定，L 是長度、P 是族群數、TopK 是放幾個高手開局，線往上爬，代表演算法一代一代找到更好的建造順序。左邊圖一是全部隨機的傳統做法，染色體長度 200 的兩條線明顯落後，實際跑的 153 代都不夠它收斂。右邊圖二加了高手開局：藍線是全部隨機，從負一千以下慢慢爬，分數會是負的，應該就是懲罰函數裡的 N：隨機的方案很多格沒達標，每格都被扣 N；只要放進一個以上的高手建造順序，第零代就在負一百到負三百之間，而且染色體越長差距越明顯。");
}

// ================================================================ 10. table 3: the conclusion first, then the two rows of the paper's table that back it
{
  const s = slide("論文內容", "加入高手開局，更好也更穩");
  mapTag(s, "地圖：跑完的結果");
  // two big numbers: what the audience should remember
  [[0.6, "36 → 240～253", "長度 200 的平均分數（30 次）"], [5.15, "±182 → ±17～47", "標準差：放了高手開局，每次結果都差不多"]].forEach(([x, big, label]) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 1.25, w: 4.25, h: 1.0, rectRadius: 0.1, fill: { color: C.background2 }, line: { type: "none" }, objectName: "stat card" });
    text(s, big, { x, y: 1.28, w: 4.25, h: 0.6, fontSize: 30, bold: true, color: C.accent1, align: "center", valign: "middle" });
    text(s, label, { x, y: 1.88, w: 4.25, h: 0.3, fontSize: 12, color: C.text2, align: "center" });
  });
  // the evidence: header + all seven length-200 rows cut from the paper's Table 3 (1075 x 353 px; rows are 39 px from y = 80)
  const T = { x: 2.0, y: 2.45, w: 6.0 };
  T.h = T.w * 353 / 1075;
  image(s, "paper_table3_len200_all.png", T.x, T.y, T.w, T.h, "論文表 3 節錄：長度 200 時，放 0 到 200 個高手開局的結果");
  const row = (px) => T.y + T.w * px / 1075;
  // frame the two groups: random (grey) vs. any number of pro openings (blue)
  [[80, 119, C.accent3, "全部隨機"], [119, 353, C.accent1, "放高手開局"]].forEach(([top, bottom, color, label]) => {
    s.addShape(pres.shapes.RECTANGLE, { x: T.x - 0.05, y: row(top), w: T.w + 0.1, h: row(bottom) - row(top), fill: { type: "none" }, line: { color, width: 1.5 }, objectName: "row group" });
    text(s, label, { x: 0.6, y: row(top), w: 1.3, h: row(bottom) - row(top), fontSize: 11, bold: true, color, align: "right", valign: "middle" });
  });
  text(s, "放幾個差不多，時間也沒變多；作者的結論是整體以 1/4 高手開局、3/4 隨機最好。", { x: 0.6, y: 4.6, w: 8.8, h: 0.35, fontSize: 12.5, bold: true, color: C.text1, align: "center" });
  source(s, "數字：原文表 3 節錄（長度 200、族群 200、各跑 30 次；TopK = 放進幾個高手開局）");
  s.addNotes("（約 30 秒）換成數字看，記上面兩個數字就好。長度 200 的時候，全部隨機平均只有 36 分，放進一個高手開局就到 240 分；標準差從正負 182 降到二十上下，每次結果都差不多。下面是論文表三長度 200 的七列：放 1 個到 200 個高手開局，平均都在 240 到 253 之間，標準差大多在 17 到 29，只有放滿 200 個時是 47；時間也沒變多，反而還少一點。所以放幾個差異不大，作者的結論是整體以四分之一高手開局、四分之三隨機最好。");
}

pres.addSection({ title: "應用到紅色警戒" });
// ================================================================ 11. RA2 units: what a plan is made of
{
  const s = slide("應用到紅色警戒", "紅色警戒 2：會用到的單位（蘇聯）");
  const units = [
    ["APOC", "天啟坦克", 1750, "主力，對地對空都能打"],
    ["HTK", "防空履帶車", 500, "便宜防空，擋戰機和空降"],
    ["HTNK", "犀牛坦克", 900, "前期主力坦克"],
    ["E2", "動員兵", 100, "最便宜的步兵"],
    ["SHK", "磁爆步兵", 500, "電擊步兵，對步兵、建築很強"],
    ["ZEP", "基洛夫空艇", 2000, "飛行轟炸，專門拆建築"],
    ["BORIS", "菁英戰鬥兵", 1500, "英雄步兵，能呼叫空襲"],
    ["HARV", "武裝採礦車", 1400, "採礦賺錢，經濟來源"],
  ];
  // game cover on the left: this is where the audience first meets RA2
  s.addImage({ path: IMG("Cncra2-win-cover.jpg"), x: 0.6, y: 1.25, w: 1.45, h: 2.06, altText: "紅色警戒 2 遊戲封面", objectName: "ra2 cover" });
  text(s, "紅色警戒 2", { x: 0.6, y: 3.36, w: 1.45, h: 0.3, fontSize: 11, bold: true, color: C.text2, align: "center" });
  units.forEach(([id, name, cost, role], i) => {
    const x = 2.35 + (i % 4) * 1.78, y = 1.25 + Math.floor(i / 4) * 1.7;
    s.addImage({ path: IMG(`units/${id}.png`), x, y, w: 1.0, h: 0.8, altText: `${name}的遊戲圖示`, objectName: `cameo ${id}` });
    text(s, `$${cost}`, { x: x + 1.05, y, w: 0.68, h: 0.3, fontSize: 11, bold: true, color: C.accent1 });
    text(s, role, { x, y: y + 0.86, w: 1.68, h: 0.6, fontSize: 10, color: C.text2 });
  });
  text(s, "搜尋要決定：每種單位各蓋幾個、先蓋哪些（例：犀牛 ×3、防空履帶車 ×8 → 天啟 ×6、菁英戰鬥兵 ×1）", { x: 0.6, y: 4.65, w: 8.8, h: 0.4, fontSize: 12, bold: true });
  source(s, "封面：Wikipedia；圖示：遊戲內單位圖示（紅色警戒 2 尤里的復仇，合理使用）；價格取自遊戲設定檔 rulesmd.ini");
  s.addNotes("（約 45 秒）論文介紹完了，最後講我可以拿它做什麼。我的題目是紅色警戒 2，一款跟星海爭霸很像的即時戰略遊戲，目標是打贏遊戲裡最難的困難 AI，而且越快越好。我玩的是蘇聯，會用到這些單位：天啟坦克是主力，最耐打；防空履帶車便宜，用來擋困難 AI 的戰機和空降；犀牛坦克是前期的坦克；動員兵最便宜；磁爆步兵對步兵和建築很強；基洛夫空艇專門拆建築；菁英戰鬥兵是英雄步兵，可以呼叫空襲；採礦車負責賺錢。我要找的，就是每種單位各蓋幾個、先蓋哪些，這就對應到論文的建造順序。怎麼找的先賣個關子，期末再講；先給大家看目前的進度。");
}

// ================================================================ 12. RA2 teaser: the model already beats the easy AI (method saved for the final report)
{
  const s = slide("應用到紅色警戒", "應用到紅色警戒 2：目前能自動打贏簡單 AI");
  s.addMedia({ type: "video", path: V + "rush_easy.mp4", cover: "data:image/png;base64," + fs.readFileSync(V + "rush_cover.png").toString("base64"), x: 1.63, y: 1.25, w: 6.75, h: 3.8, objectName: "rush video" });
  source(s, "實機自動對戰錄影，加速播放（56 秒）；約 5.4 分鐘獲勝");
  s.addNotes("（約 60 秒，邊播邊講）這是一場完整的實機對戰，加速到一分鐘內。程式自己開遊戲、進遭遇戰，不碰滑鼠，建造順序是模型自己決定的。部隊在敵方基地外集合，到齊才一起進攻，先打會反擊的單位，再拆建造場，大約五分半鐘打贏簡單 AI。");
}

// ================================================================ 13. wrap-up + what comes in the final report
{
  const s = slide("應用到紅色警戒", "總結與期末預告");
  text(s, "這篇論文", { x: 0.6, y: 1.3, w: 4.2, h: 0.35, fontSize: 16, bold: true, color: C.accent1 });
  bullets(s, [
    ["兵要出多少：", "從錄影學門檻 ξ（p = 0.5），用懲罰函數放進評分"],
    ["從哪裡開始找：", "高手開局（TopK）放進初始族群"],
    ["怎麼搜尋：", "基因演算法三個弱點各補一招：懲罰函數、模擬退火、高手開局"],
    ["實際證明：", "同樣的基因演算法＋模擬退火，加高手開局後更好也更穩（36 → 240）"],
    ["沒有驗證：", "ξ 是否比專家給的好、在真實遊戲有沒有用"],
  ], { x: 0.6, y: 1.75, w: 4.3, h: 2.5, fontSize: 12 });
  text(s, "期末預告：紅色警戒 2", { x: 5.2, y: 1.3, w: 4.2, h: 0.35, fontSize: 16, bold: true, color: C.accent1 });
  bullets(s, [
    "怎麼把 ξ 和 TopK 搬到紅警",
    "勝率和速度兩個目標怎麼兼顧",
    "挑戰最難的困難 AI",
  ], { x: 5.2, y: 1.75, w: 4.2, h: 2.4 });
  text(s, "謝謝大家", { x: 0.6, y: 4.3, w: 8.8, h: 0.6, fontSize: 24, bold: true, color: C.text1, align: "center" });
  s.addNotes("（約 35 秒）最後總結：這篇論文只回答兩個問題，兵要出多少、從哪裡開始找，答案都是看資料：從錄影學門檻 ξ，再用高手開局當起點；搜尋用基因演算法，它的三個弱點各用懲罰函數、模擬退火、高手開局補起來。不過要注意，實驗真正證明的是：同樣的基因演算法加模擬退火，用高手開局當起點，結果更好也更穩；至於學出來的 ξ 是不是比專家給的好、找到的建造順序在真實遊戲有沒有用，論文沒有做實驗。紅色警戒的部分，目前已經能自動打贏簡單 AI；怎麼把這套方法搬過去、能不能打贏最難的困難 AI，期末再跟大家報告。謝謝大家。");
}

pres.writeFile({ fileName: OUT }).then(() => applyTheme(OUT, THEME)).then(() => console.log("wrote " + OUT));
