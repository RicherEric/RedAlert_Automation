const pptxgen = require("pptxgenjs");
const path = require("path");
const { applyTheme } = require("C:/Users/ycker/.claude/skills/synced/84829876-5bcd-4ab2-8d53-55a7427dd88c_445b74f3-7a38-430c-8857-bbde22bf7bfa/pptx/scripts/apply_theme.js");

const OUT = process.argv[2] || "deck.pptx";
const IMG = (n) => path.join(__dirname, "img", n);
const GAME = (n) => path.join("D:/RedAlert_Automation/thesis/images", n);

const THEME = {
  name: "RTS Build Order",
  headFontFace: "Microsoft JhengHei",
  bodyFontFace: "Microsoft JhengHei",
  colors: {
    dk1: "14213D", lt1: "FFFFFF", dk2: "2B3A55", lt2: "EEF1F6",
    accent1: "FCA311", accent2: "3A6EA5", accent3: "8D99AE", accent4: "E63946",
    accent5: "2A9D8F", accent6: "6C757D", hlink: "3A6EA5", folHlink: "6C757D",
  },
};

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9"; // 10 x 5.625 in
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "論文導讀：資料驅動的建造順序最佳化";
const C = pres.SchemeColor;

// ---------------------------------------------------------------- layouts
pres.defineSlideMaster({
  title: "TITLE",
  background: { color: THEME.colors.dk1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 1.35, w: 5.5, h: 1.5, fontSize: 30, bold: true, color: C.background1, valign: "bottom", align: "left", margin: 0 }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.6, y: 3.05, w: 5.5, h: 1.9, fontSize: 13, color: C.background2, valign: "top", margin: 0 }, text: "" } },
  ],
});
pres.defineSlideMaster({
  title: "SECTION",
  background: { color: THEME.colors.dk1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 2.0, w: 8.8, h: 1.0, fontSize: 32, bold: true, color: C.background1, margin: 0 }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.6, y: 3.05, w: 8.8, h: 0.8, fontSize: 16, color: C.accent1, margin: 0 }, text: "" } },
  ],
});
pres.defineSlideMaster({
  title: "CONTENT",
  background: { color: THEME.colors.lt1 },
  margin: [0.5, 0.5, 0.5, 0.5],
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.5, y: 0.28, w: 9.0, h: 0.72, fontSize: 26, bold: true, color: C.text1, valign: "middle", align: "left", margin: 0 }, text: "" } },
    { text: { text: "論文導讀｜Wang et al., CCC 2019", options: { x: 0.5, y: 5.22, w: 5, h: 0.25, fontSize: 9, color: C.accent3, margin: 0 } } },
  ],
  slideNumber: { x: 9.0, y: 5.22, w: 0.5, h: 0.25, fontSize: 9, color: C.accent3, align: "right" },
});

// ---------------------------------------------------------------- helpers
let n = 0;
const content = (section, title) => {
  const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: section });
  s.addText(title, { placeholder: "title" });
  return s;
};
const shadow = () => ({ type: "outer", color: "000000", opacity: 0.18, blur: 6, offset: 2, angle: 90 });
const card = (s, x, y, w, h, name) => s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
  x, y, w, h, rectRadius: 0.08, fill: { color: C.background2 }, line: { color: C.background2 }, objectName: name,
});
const badge = (s, x, y, label, name, d = 0.42) => {
  s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: C.accent1 }, line: { color: C.accent1 }, objectName: name });
  s.addText(String(label), { x, y, w: d, h: d, fontSize: 14, bold: true, color: C.text1, align: "center", valign: "middle", margin: 0, isTextBox: true, objectName: name + " label" });
};
const text = (s, t, o) => s.addText(t, { isTextBox: true, margin: 0, color: C.text1, fontSize: 14, valign: "top", ...o });
const figure = (s, file, x, y, w, h, alt, name) => {
  s.addShape(pres.shapes.RECTANGLE, { x: x - 0.08, y: y - 0.08, w: w + 0.16, h: h + 0.16, fill: { color: C.background1 }, line: { color: "D9DEE7", width: 0.75 }, shadow: shadow(), objectName: name + " frame" });
  s.addImage({ path: path.isAbsolute(file) ? file : IMG(file), x, y, w, h, altText: alt, objectName: name });
};
const bullets = (s, items, o) => s.addText(
  items.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < items.length - 1, paraSpaceAfter: 6 } })),
  { isTextBox: true, margin: 0, color: C.text1, fontSize: 14, valign: "top", ...o });
const source = (s, t) => text(s, t, { x: 0.5, y: 4.95, w: 9, h: 0.24, fontSize: 9, color: C.accent3 });

// ================================================================ 1. title
pres.addSection({ title: "開場" });
{
  const s = pres.addSlide({ masterName: "TITLE", sectionTitle: "開場" });
  s.addText("用高手錄影，教基因演算法排「建造順序」", { placeholder: "title" });
  s.addText([
    { text: "論文導讀：A Data-driven Approach to Solve a Production Constrained Build-order Optimization Problem", options: { breakLine: true } },
    { text: " ", options: { breakLine: true, fontSize: 8 } },
    { text: "Pengpeng Wang, Yifeng Zeng, Bilian Chen, Langcai Cao｜廈門大學・Teesside University", options: { breakLine: true, color: C.accent3, fontSize: 12 } },
    { text: "第 38 屆中國控制會議 CCC 2019", options: { color: C.accent3, fontSize: 12 } },
  ], { placeholder: "body" });
  s.addShape(pres.shapes.OVAL, { x: 0.6, y: 0.75, w: 0.42, h: 0.42, fill: { color: C.accent1 }, line: { color: C.accent1 }, objectName: "title badge" });
  s.addImage({ path: GAME("StarCraft_box_art.jpg"), x: 6.45, y: 1.55, w: 1.58, h: 2.0, altText: "星海爭霸遊戲封面", objectName: "starcraft cover", shadow: shadow() });
  s.addImage({ path: GAME("Cncra2-win-cover.jpg"), x: 8.1, y: 1.55, w: 1.41, h: 2.0, altText: "紅色警戒 2 遊戲封面", objectName: "ra2 cover", shadow: shadow() });
  text(s, "論文：星海爭霸　→　應用：紅色警戒 2", { x: 6.45, y: 3.7, w: 3.06, h: 0.3, fontSize: 10, color: C.accent3, align: "center" });
  text(s, "封面圖片來源：Wikipedia（合理使用）", { x: 6.45, y: 4.95, w: 3.06, h: 0.25, fontSize: 8, color: C.accent3, align: "center" });
  s.addNotes("大家好，今天要分享的這篇是 2019 年中國控制會議的論文。一句話講完它在做什麼：拿星海爭霸職業選手的錄影當資料，幫基因演算法決定「開局要先蓋什麼、出什麼兵」。等一下我會先講它的問題、方法和結果，最後花兩三頁講我怎麼把它用在我自己的紅色警戒專案上。");
}

// ================================================================ 2. one-minute summary
pres.addSection({ title: "論文內容" });
{
  const s = content("論文內容", "一分鐘看懂這篇在做什麼");
  const cols = [
    ["問題", "星海爭霸開局要排「先蓋什麼、再出什麼兵」。而且每個時段兵要夠多才擋得住對手——但「要多少才夠」以前都是專家憑感覺給的。"],
    ["做法", "拿 459 場職業錄影，統計對手每個時段出多少兵，當作「至少要有」的門檻 ξ；再拿高手的建造順序當基因演算法的起點。"],
    ["結果", "分數平均更高、也更穩定：長度 200 時標準差從 ±182 降到 ±21，而且幾乎不多花時間。"],
  ];
  cols.forEach(([h, b], i) => {
    const x = 0.5 + i * 3.05;
    card(s, x, 1.3, 2.85, 2.75, `card ${h}`);
    badge(s, x + 0.25, 1.55, i + 1, `badge ${h}`);
    text(s, h, { x: x + 0.8, y: 1.55, w: 1.9, h: 0.42, fontSize: 20, bold: true, valign: "middle" });
    text(s, b, { x: x + 0.25, y: 2.2, w: 2.35, h: 1.75, fontSize: 14, color: C.text2 });
  });
  text(s, "一句話：讓資料決定限制參數，再用高手的解當搜尋起點。", { x: 0.5, y: 4.3, w: 9, h: 0.45, fontSize: 16, bold: true, color: C.accent2 });
  s.addNotes("先給大家一個全貌。問題是：星海爭霸開局要排建造順序，而且每個時段的兵要夠多，不然會被對手打爆。難的是「到底要多少才夠」，以前都是專家憑經驗設一個數字。這篇的做法很直覺：既然不知道，那就去看職業選手的錄影，統計對手在每個時段實際出了多少兵，拿來當門檻。另外，他們也把高手的建造順序拿來當基因演算法的初始解。結果是分數比較高，而且穩定很多。");
}

// ================================================================ 3. background
{
  const s = content("論文內容", "背景：什麼是 Build-order？");
  text(s, "就像煮菜步驟：沒有兵營就不能出陸戰隊。目標是在限制下，最快拿到最多的兵。", { x: 0.5, y: 1.2, w: 4.25, h: 0.75, fontSize: 14, bold: true });
  figure(s, GAME("Zerg_colony_(StarCraft).png"), 0.6, 2.15, 3.6, 2.69, "星海爭霸遊戲畫面：神族部隊進攻蟲族基地", "starcraft shot");
  const tiles = [["人口", "Supply", "人口不夠就不能出新兵"], ["科技樹", "Technology", "先有兵營才能蓋工廠"], ["資源", "Resource", "礦物和瓦斯"], ["時間", "Time", "每個建築、單位都有建造時間"]];
  tiles.forEach(([zh, en, d], i) => {
    const x = 5.1 + (i % 2) * 2.25, y = 1.25 + Math.floor(i / 2) * 1.75;
    card(s, x, y, 2.05, 1.55, `tile ${en}`);
    text(s, zh, { x: x + 0.2, y: y + 0.18, w: 1.65, h: 0.4, fontSize: 18, bold: true });
    text(s, en, { x: x + 0.2, y: y + 0.58, w: 1.65, h: 0.3, fontSize: 11, color: C.accent2 });
    text(s, d, { x: x + 0.2, y: y + 0.9, w: 1.7, h: 0.55, fontSize: 12, color: C.text2 });
  });
  s.addNotes("先講背景。Build-order 就是建造順序，大家可以想成煮菜步驟：沒有兵營就不能出陸戰隊，沒有錢、沒有人口也不能出兵，每樣東西還要花時間。論文把這些叫做遊戲本身的四種限制：人口、科技樹、資源、時間。最佳化的目標，就是在這些限制下，最快達成目標，或在固定時間內拿到最多的兵。");
}

// ================================================================ 4. pain point
{
  const s = content("論文內容", "卡關的地方：兵到底要出多少？");
  card(s, 0.5, 1.3, 3.4, 3.45, "xi card");
  text(s, "ξ = ?", { x: 0.5, y: 1.75, w: 3.4, h: 1.3, fontSize: 60, bold: true, color: C.accent1, align: "center", valign: "middle" });
  text(s, "每個時段、每種兵「至少要有幾個」", { x: 0.75, y: 3.25, w: 2.9, h: 0.9, fontSize: 14, color: C.text2, align: "center" });
  bullets(s, [
    "只追求「兵越多越好」不合理：有些兵很貴、很難出",
    "所以要加限制：每個時段每種兵至少 ξ 個",
    "但 ξ 跟對手出多少兵有關，而戰爭迷霧讓你看不到對手",
    "以前的做法：專家主觀給一個值 → 算出來的「最佳解」實戰常常不如預期",
  ], { x: 4.3, y: 1.4, w: 5.2, h: 3.3, fontSize: 15 });
  s.addNotes("這篇最核心的痛點在這裡。如果只追求兵越多越好，演算法會一直出最便宜的兵，這不合理。所以要加一個限制：每個時段每種兵至少要有 ξ 個。問題是 ξ 要設多少？它跟對手出多少兵有關，可是遊戲有戰爭迷霧，你看不到對手。以前的論文都是專家拍腦袋給一個數字，所以算出來的最佳解，放到實戰常常不好用。");
}

// ================================================================ 5. formulation
{
  const s = content("論文內容", "數學上怎麼寫：其實很單純");
  figure(s, "eq123.png", 0.6, 1.35, 4.27, 2.8, "論文式 (1)(2)(3)：目標函數為各時段各兵種數量總和最大化，限制為遊戲規則與每項至少 ξ", "eq 1-3");
  const rows = [
    ["式 (1)", "目標：4 個時段 × 6 種兵的數量全部加起來，越多越好"],
    ["式 (2)", "遊戲本身的規則：人口、科技樹、資源、時間"],
    ["式 (3)", "每個時段、每種兵至少 ξ 個 ← 這篇要解決的就是 ξ"],
  ];
  rows.forEach(([k, v], i) => {
    const y = 1.35 + i * 1.0;
    badge(s, 5.35, y + 0.05, i + 1, `eq badge ${i + 1}`);
    text(s, k, { x: 5.95, y, w: 3.5, h: 0.32, fontSize: 15, bold: true });
    text(s, v, { x: 5.95, y: y + 0.34, w: 3.5, h: 0.6, fontSize: 13, color: C.text2 });
  });
  source(s, "圖片來源：原文第 3.2 節，式 (1)–(3)");
  s.addNotes("數學式其實很單純。式一是目標：把每個時段、每種兵的數量全部加起來，越多越好。式二是遊戲本身的規則。式三就是剛剛講的門檻：每個時段每種兵至少 ξ 個。整篇論文的重點，就是怎麼把這個 ξ 設得有根據。");
}

// ================================================================ 6. penalty
{
  const s = content("論文內容", "懲罰函數：沒達標就扣大分");
  figure(s, "eq45.png", 0.6, 1.4, 4.6, 1.98, "論文式 (4)(5)：懲罰函數，未達門檻時額外扣 N", "eq 4-5");
  card(s, 5.6, 1.3, 3.9, 1.45, "penalty up");
  text(s, "有達到門檻", { x: 5.85, y: 1.45, w: 3.4, h: 0.35, fontSize: 15, bold: true, color: C.accent5 });
  text(s, "超過多少就加多少分，照常計算", { x: 5.85, y: 1.85, w: 3.4, h: 0.7, fontSize: 13, color: C.text2 });
  card(s, 5.6, 2.95, 3.9, 1.45, "penalty down");
  text(s, "沒達到門檻", { x: 5.85, y: 3.1, w: 3.4, h: 0.35, fontSize: 15, bold: true, color: C.accent4 });
  text(s, "除了差的那些，再多扣一個很大的 N（作者設成解的複雜度）", { x: 5.85, y: 3.5, w: 3.4, h: 0.8, fontSize: 13, color: C.text2 });
  text(s, "白話：把「硬限制」變成「軟限制」，基因演算法才分得出「差一點」和「差很多」的解。", { x: 0.6, y: 3.95, w: 4.6, h: 0.8, fontSize: 14, bold: true });
  source(s, "圖片來源：原文第 3.2 節，定義 1、式 (4)–(5)");
  s.addNotes("但硬限制對基因演算法不友善，因為不合格的解一律丟掉，就看不出誰比較接近合格。所以作者用了懲罰函數：有達到門檻就照常加分；沒達到，除了差多少扣多少，還額外扣一個很大的 N。這樣演算法就能分辨「差一點」跟「差很多」，往對的方向演化。");
}

// ================================================================ 7. learning xi
{
  const s = content("論文內容", "重點來了：ξ 怎麼從錄影學出來");
  const steps = [
    ["跑錄影", "459 場職業錄影，每 12 幀記下看到的敵方單位"],
    ["變向量", "每位玩家得到一個「時段 × 兵種」的數量向量"],
    ["排名次", "用作者定義的「支配」規則排序（實際上就是比總數）"],
    ["取分位", "取第 N·p 名當 ξ；p = 0.5 就是中位數"],
  ];
  steps.forEach(([h, b], i) => {
    const x = 0.5 + i * 2.3;
    card(s, x, 1.3, 2.1, 2.05, `step ${i + 1}`);
    badge(s, x + 0.2, 1.48, i + 1, `step badge ${i + 1}`);
    text(s, h, { x: x + 0.75, y: 1.48, w: 1.25, h: 0.42, fontSize: 16, bold: true, valign: "middle" });
    text(s, b, { x: x + 0.2, y: 2.05, w: 1.75, h: 1.2, fontSize: 12, color: C.text2 });
    if (i < steps.length - 1) s.addShape(pres.shapes.RIGHT_ARROW, { x: x + 2.12, y: 2.2, w: 0.16, h: 0.26, fill: { color: C.accent1 }, line: { color: C.accent1 }, objectName: `arrow ${i + 1}` });
  });
  figure(s, "eq6.png", 0.6, 3.75, 4.6, 0.46, "論文式 (6)：觀察到的敵方兵量不超過我方產量的機率至少為 p", "eq 6");
  text(s, "白話：我的出兵量，要至少贏過 p 比例的對手。p 越高越保守。", { x: 5.55, y: 3.62, w: 3.95, h: 0.75, fontSize: 14, bold: true });
  source(s, "圖片來源：原文第 3.3 節，式 (6)");
  s.addNotes("這頁是整篇最有價值的部分。作者把 459 場錄影跑一遍，每 12 幀記下玩家看到的敵方單位，整理成「時段乘兵種」的向量。然後定義一個支配關係來排序，論文花了不少篇幅證明它有遞移性，但講白了就是按總數排。最後取第 N 乘 p 名當 ξ。p 等於 0.5 就是中位數。式六的意思用白話講：我的出兵量要至少贏過 p 比例的對手，p 調越高就越保守。");
}

// ================================================================ 8. table 1
{
  const s = content("論文內容", "學出來的門檻長什麼樣子（p = 0.5）");
  figure(s, "table1.png", 0.6, 1.3, 8.8, 2.05, "論文表 1：p=0.5 時，四個時段六種單位的門檻值", "table 1");
  const notes = [
    ["第 1 時段（0–3 分）", "只有 0.57 個陸戰隊，開局大家都在發展"],
    ["第 4 時段（9–12 分）", "攻城坦克要 12.43 台、歌利亞 6.57 台"],
    ["結論", "人族內戰中期是坦克大戰，跟職業選手的常識一致"],
  ];
  notes.forEach(([h, b], i) => {
    const x = 0.5 + i * 3.05;
    text(s, h, { x, y: 3.7, w: 2.85, h: 0.35, fontSize: 14, bold: true, color: C.accent2 });
    text(s, b, { x, y: 4.07, w: 2.85, h: 0.75, fontSize: 13, color: C.text2 });
  });
  source(s, "圖片來源：原文表 1（12 種單位中，全為 0 的已剔除，剩 6 種）");
  s.addNotes("這是學出來的門檻。第一個時段，也就是開局三分鐘內，只有零點五七個陸戰隊，大家都在發展經濟。到第四個時段，攻城坦克要十二台以上、歌利亞六台多。這跟職業選手的常識一致：人族內戰中期就是坦克大戰。也就是說，資料學出來的門檻是合理的。另外 12 種兵裡，全部是 0 的被拿掉了，最後剩 6 種。");
}

// ================================================================ 9. algorithm
{
  const s = content("論文內容", "怎麼求解：改良版基因演算法");
  const parts = [
    ["基因演算法", "一條染色體 = 一個建造順序，長度 100 / 150 / 200 步"],
    ["模擬退火", "加強局部搜尋，避免卡在爛解出不來"],
    ["高手開局", "從錄影抽出 799 / 715 / 624 個建造順序，依分數排序取前 TopK 個放進初始族群"],
    ["自製模擬器", "簡化採礦和建造流程，快速評估每一條染色體"],
  ];
  parts.forEach(([h, b], i) => {
    const x = 0.5 + (i % 2) * 4.6, y = 1.3 + Math.floor(i / 2) * 1.78;
    card(s, x, y, 4.4, 1.6, `algo ${h}`);
    badge(s, x + 0.25, y + 0.25, i + 1, `algo badge ${i + 1}`);
    text(s, h, { x: x + 0.85, y: y + 0.25, w: 3.3, h: 0.42, fontSize: 17, bold: true, valign: "middle" });
    text(s, b, { x: x + 0.85, y: y + 0.75, w: 3.35, h: 0.75, fontSize: 13, color: C.text2 });
  });
  s.addNotes("求解的部分是改良版基因演算法，有四個零件。第一，染色體就是建造順序，長度試了 100、150、200 步。第二，加了模擬退火來加強局部搜尋。第三，也是這篇的另一個重點：把高手錄影裡的建造順序先跑一次算分，排好序，取前 K 名放進初始族群，而不是全部隨機。第四，自己寫了一個簡化的星海模擬器，讓每條染色體可以很快被評分。");
}

// ================================================================ 10. setup
{
  const s = content("論文內容", "實驗怎麼設計");
  const stats = [["459", "場人族內戰錄影", "從 7659 場職業錄影中篩出"], ["4 × 3", "分鐘的時段", "只看開局前 12 分鐘"], ["6", "種兵", "12 種裡有出現過的"], ["30", "次獨立重複", "每組參數都跑 30 次取平均"]];
  stats.forEach(([big, label, sub], i) => {
    const x = 0.5 + i * 2.3;
    card(s, x, 1.3, 2.1, 2.2, `stat ${i + 1}`);
    text(s, big, { x, y: 1.45, w: 2.1, h: 0.95, fontSize: 40, bold: true, color: C.accent2, align: "center", valign: "middle" });
    text(s, label, { x: x + 0.1, y: 2.45, w: 1.9, h: 0.35, fontSize: 14, bold: true, align: "center" });
    text(s, sub, { x: x + 0.1, y: 2.85, w: 1.9, h: 0.55, fontSize: 11, color: C.text2, align: "center" });
  });
  text(s, "參數：交配率 0.9、突變率 0.05、初始溫度 1000、降溫率 0.9、最多 200 代（實際約 153 代）；族群 150 / 200；MATLAB 單執行緒。比較對象：初始解全部隨機的傳統做法。", { x: 0.5, y: 3.8, w: 9, h: 0.8, fontSize: 13, color: C.text2 });
  s.addNotes("實驗設定很簡單。資料用的是公開的職業錄影集，7659 場裡挑出人族打人族的 459 場。開局切成四個時段，每段三分鐘。兵種剩 6 種。每組參數都獨立跑 30 次取平均，比較的對象是初始解全部隨機的傳統做法。");
}

// ================================================================ 11. fig 1
{
  const s = content("論文內容", "沒有高手資料：染色體一長就收斂不了");
  figure(s, "fig1.png", 0.6, 1.3, 4.15, 3.38, "論文圖 1：不使用玩家建造順序時，各參數組合的平均適應度隨世代變化", "fig 1");
  bullets(s, [
    "族群越大，分數越好，但跑越久",
    "長度 200 的兩條線（綠、淺藍）明顯落後：200 代不夠它演化",
    "長度 200、族群 150 最後平均是 −32：還沒達到門檻",
    "標準差很大（±100 ~ ±234）：每次跑的結果差很多",
  ], { x: 5.15, y: 1.4, w: 4.35, h: 3.3, fontSize: 14 });
  source(s, "圖片來源：原文圖 1、表 2");
  s.addNotes("先看傳統做法，也就是初始解全部隨機。族群越大分數越好，但也跑越久。最明顯的是長度 200 的兩條線，綠色和淺藍色，明顯落後，因為搜尋空間太大，200 代不夠它演化。看表二的數字，長度 200、族群 150，最後平均還是負的，代表連門檻都沒達到。而且標準差非常大，每次跑的結果差很多。");
}

// ================================================================ 12. fig 2
{
  const s = content("論文內容", "加入高手開局：起跑點直接贏一大截");
  figure(s, "fig2.png", 0.6, 1.3, 8.8, 2.3, "論文圖 2：使用不同數量玩家建造順序時的平均適應度，(a)(b)(c) 分別為染色體長度 100、150、200", "fig 2");
  const notes = [
    ["藍色那條", "TopK0：初始解全部隨機，從 −1000 慢慢爬"],
    ["其他顏色", "只要放 1 個以上的高手開局，第 0 代就從 −200 左右起跳"],
    ["越長越明顯", "長度 200 時，隨機版到最後都追不上"],
  ];
  notes.forEach(([h, b], i) => {
    const x = 0.5 + i * 3.05;
    text(s, h, { x, y: 3.95, w: 2.85, h: 0.32, fontSize: 14, bold: true, color: C.accent2 });
    text(s, b, { x, y: 4.3, w: 2.85, h: 0.6, fontSize: 12, color: C.text2 });
  });
  source(s, "圖片來源：原文圖 2（族群 200，由左至右染色體長度 100 / 150 / 200）");
  s.addNotes("再來看加了高手開局的結果。三張圖是三種染色體長度。藍色那條是全部隨機的版本，從負一千慢慢往上爬。其他顏色只要放進一個以上的高手建造順序，第零代就直接從負兩百左右起跳，而且很快收斂。染色體越長，差距越明顯，長度 200 的時候，隨機版跑到最後都追不上。");
}

// ================================================================ 13. chart from table 3
{
  const s = content("論文內容", "數字會說話：更好，也更穩");
  s.addChart(pres.charts.BAR, [
    { name: "全部隨機（TopK 0）", labels: ["長度 100", "長度 150", "長度 200"], values: [109.27, 154.40, 35.87] },
    { name: "1 個高手開局", labels: ["長度 100", "長度 150", "長度 200"], values: [187.67, 241.40, 239.67] },
    { name: "50 個高手開局", labels: ["長度 100", "長度 150", "長度 200"], values: [163.73, 250.73, 252.60] },
  ], {
    x: 0.5, y: 1.25, w: 5.6, h: 3.55, barDir: "col", barGapWidthPct: 60,
    chartColors: [THEME.colors.accent3, THEME.colors.accent2, THEME.colors.accent1],
    showTitle: true, title: "30 次平均最佳適應度（族群 200）", titleFontSize: 12, titleColor: THEME.colors.dk1, titleFontFace: "+mn-lt",
    showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 9, dataLabelColor: THEME.colors.dk2, dataLabelFormatCode: "0", dataLabelFontFace: "+mn-lt",
    showLegend: true, legendPos: "b", legendFontSize: 10, legendColor: THEME.colors.dk2, legendFontFace: "+mn-lt",
    catAxisLabelColor: THEME.colors.dk2, valAxisLabelColor: THEME.colors.accent3, catAxisLabelFontSize: 11, valAxisLabelFontSize: 9,
    catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt",
    valGridLine: { color: "E3E7EE", size: 0.5 }, catGridLine: { style: "none" }, valAxisMinVal: 0,
    objectName: "table 3 chart", altText: "依原文表 3 繪製的長條圖：使用高手開局後，三種染色體長度的平均最佳適應度皆提高",
  });
  const calls = [["±182 → ±21", "長度 200 的標準差：用了高手開局，每次結果都差不多"], ["約 1/4", "作者建議：初始族群 1/4 用高手開局，3/4 隨機"], ["≤ +10%", "多花的時間很少（長度 100、150 約多 8~10%），長度 200 甚至還快 7%"]];
  calls.forEach(([big, b], i) => {
    const y = 1.3 + i * 1.18;
    text(s, big, { x: 6.45, y, w: 3.05, h: 0.5, fontSize: 24, bold: true, color: C.accent2 });
    text(s, b, { x: 6.45, y: y + 0.5, w: 3.05, h: 0.6, fontSize: 12, color: C.text2 });
  });
  source(s, "資料來源：依原文表 3 重新繪製");
  s.addNotes("這張是我把表三重新畫成長條圖。灰色是全部隨機，藍色是只放一個高手開局，橘色是放五十個。三種長度都明顯變好，長度 200 的時候從 36 分跳到 240 分以上。更重要的是穩定度：標準差從正負 182 降到正負 21，代表每次跑結果都差不多。時間方面只多一點點，長度 200 甚至還快一點。作者最後的建議是初始族群四分之一用高手開局、四分之三隨機，效果最好。");
}

// ================================================================ 14. pros & cons
{
  const s = content("論文內容", "優點，以及值得討論的地方");
  card(s, 0.5, 1.3, 4.4, 2.85, "pros");
  text(s, "做得好的地方", { x: 0.75, y: 1.45, w: 3.9, h: 0.4, fontSize: 17, bold: true, color: C.accent5 });
  bullets(s, [
    "用真實資料取代專家主觀設定",
    "方法簡單，很容易搬到別的遊戲",
    "高手解當起點，收斂快又穩",
  ], { x: 0.75, y: 2.0, w: 3.95, h: 2.05, fontSize: 14 });
  card(s, 5.1, 1.3, 4.4, 2.85, "cons");
  text(s, "可以追問的地方", { x: 5.35, y: 1.45, w: 3.9, h: 0.4, fontSize: 17, bold: true, color: C.accent4 });
  bullets(s, [
    "只做人族內戰（TvT）",
    "目標只算「兵的數量」，沒算打不打得贏",
    "模擬器是簡化版，沒驗證跟真遊戲差多少",
    "ξ 是固定的，對手換打法就不準",
    "p、N 等參數還是要手調",
  ], { x: 5.35, y: 2.0, w: 3.95, h: 2.05, fontSize: 14 });
  text(s, "我的評價：概念簡單但有效，是「讓資料決定參數」很好的入門例子。", { x: 0.5, y: 4.4, w: 9, h: 0.4, fontSize: 15, bold: true, color: C.accent2 });
  s.addNotes("整理一下優缺點。優點：用資料取代專家拍腦袋，方法簡單，換個遊戲也能用，而且高手解當起點效果很好。可以追問的地方也不少：只做人族內戰；目標只算兵的數量，沒有算到底打不打得贏；模擬器是簡化版，沒驗證跟真遊戲差多少；ξ 是固定值，對手換打法就失準；最後 p 和 N 這些參數還是要人手調。");
}

// ================================================================ 15. section: RA2
pres.addSection({ title: "用在紅色警戒" });
{
  const s = content("用在紅色警戒", "我的專案：讓程式自己打紅色警戒 2");
  figure(s, "D:/RedAlert_Automation/thesis/videos/shot_battle.png", 0.6, 1.35, 4.0, 3.0, "我們的實機截圖：動員兵部隊進攻 AI 基地", "ra2 shot");
  text(s, "實機截圖：我們的部隊正在進攻 AI 基地", { x: 0.6, y: 4.45, w: 4.0, h: 0.25, fontSize: 9, color: C.accent3 });
  const steps = [
    ["看", "讀遊戲記憶體（唯讀），每一幀知道雙方有什麼"],
    ["想", "經濟 + 戰鬥模擬，搜尋最快能贏的部隊和建造順序"],
    ["做", "注入 DLL 直接下指令：建造、集結、進攻、防守"],
  ];
  steps.forEach(([h, b], i) => {
    const y = 1.3 + i * 1.0;
    badge(s, 5.05, y + 0.12, h, `pipeline ${i + 1}`, 0.5);
    text(s, b, { x: 5.75, y: y + 0.05, w: 3.75, h: 0.65, fontSize: 13, valign: "middle" });
  });
  card(s, 5.05, 4.25, 4.45, 0.6, "ra2 status");
  text(s, "目前：簡單 AI 已實機打贏；中等、困難還在校正", { x: 5.25, y: 4.3, w: 4.1, h: 0.5, fontSize: 12, bold: true, color: C.accent2, valign: "middle" });
  s.addNotes("在講怎麼應用之前，先簡單介紹我的專案。我做的是讓程式自己打紅色警戒 2，分成三步：看、想、做。看，是直接讀遊戲記憶體，而且只讀不寫，每一幀都知道雙方有什麼。想，是用自己寫的經濟和戰鬥模擬器，搜尋最快能打贏的部隊組合和建造順序。做，是注入一個 DLL，直接對遊戲下指令。目前簡單難度的 AI 已經在實機打贏了，中等跟困難還在校正。");
}
{
  const s = content("用在紅色警戒", "對照：這篇論文 vs 我們的紅警專案");
  const hdr = (t, o = {}) => ({ text: t, options: { bold: true, color: C.background1, fill: { color: C.text1 }, fontSize: 13, valign: "middle", ...o } });
  const cell = (t, o = {}) => ({ text: t, options: { color: C.text1, fontSize: 12, valign: "middle", ...o } });
  const rows = [
    [hdr(""), hdr("這篇論文（星海爭霸）"), hdr("我們的紅警專案")],
    [cell("資料從哪來", { bold: true }), cell("職業選手錄影（BWAPI 重播）"), cell("讀遊戲記憶體，自動錄下每一場對 AI 的實機紀錄")],
    [cell("對手模型", { bold: true }), cell("每個時段的兵量門檻 ξ（固定值）"), cell("AI 時間軸、反攻波次、戰鬥中補兵速度、對我方兵種的應變")],
    [cell("最佳化方法", { bold: true }), cell("基因演算法 + 模擬退火"), cell("NSGA-II 多目標搜尋 + 基因演算法老師")],
    [cell("怎麼評分", { bold: true }), cell("簡化模擬器，只算兵的數量"), cell("經濟模擬 + 戰鬥模擬，算勝負和獲勝時間")],
    [cell("起點從哪來", { bold: true }), cell("高手的建造順序"), cell("以前搜尋和實機打贏的方案（TopK）＋模仿學習模型")],
  ];
  s.addTable(rows, {
    x: 0.5, y: 1.3, w: 9.0, colW: [1.6, 3.2, 4.2], rowH: [0.42, 0.62, 0.62, 0.62, 0.62, 0.62],
    border: { type: "solid", pt: 0.75, color: "D9DEE7" }, fill: { color: C.background1 }, margin: 0.08,
    objectName: "comparison table",
  });
  s.addNotes("接下來講怎麼用在我的紅色警戒專案。先做個對照。資料來源：論文用職業錄影，我們是直接讀遊戲記憶體，每打一場就自動錄下來。對手模型：論文只有一個固定門檻 ξ，我們已經量了 AI 的時間軸、反攻波次、戰鬥中補兵的速度，還有它看到我們出飛機會蓋防空。方法上，我們用多目標搜尋加基因演算法；評分上，論文只算兵的數量，我們會直接模擬打不打得贏、幾分鐘贏。");
}

// ================================================================ 16. paper method, done: quantile xi + TopK
const Q = JSON.parse(require("fs").readFileSync("D:/RedAlert_Automation/thesis/quantile_data.json", "utf8"));
const UNIT = { APOC: "天啟坦克", ZEP: "基洛夫空艇", E2: "動員兵", SHK: "磁爆步兵", HTNK: "犀牛坦克", FLAKT: "防空步兵", HTK: "防空履帶車", BORIS: "鮑里斯" };
{
  const s = content("用在紅色警戒", "照論文做①：從 AI 對局學出門檻 ξ");
  s.addChart(pres.charts.LINE, [
    { name: "p = 0.5（贏過一半對局）", labels: Q.slots.map(x => x + " 分"), values: Q.q["50"] },
    { name: "p = 0.8（贏過 80% 對局）", labels: Q.slots.map(x => x + " 分"), values: Q.q["80"] },
  ], {
    x: 0.5, y: 1.25, w: 5.6, h: 3.55, lineSize: 2.5, lineDataSymbol: "circle", lineDataSymbolSize: 7,
    chartColors: [THEME.colors.accent2, THEME.colors.accent4],
    showTitle: true, title: `困難 AI 的兵力＋防禦（$，${Q.games} 場實機紀錄）`, titleFontSize: 12, titleColor: THEME.colors.dk1, titleFontFace: "+mn-lt",
    showLegend: true, legendPos: "b", legendFontSize: 10, legendColor: THEME.colors.dk2, legendFontFace: "+mn-lt",
    catAxisLabelColor: THEME.colors.dk2, valAxisLabelColor: THEME.colors.accent3, catAxisLabelFontSize: 10, valAxisLabelFontSize: 9,
    catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt", valAxisLabelFormatCode: "#,##0",
    valGridLine: { color: "E3E7EE", size: 0.5 }, catGridLine: { style: "none" }, valAxisMinVal: 0,
    objectName: "quantile chart", altText: "困難 AI 每 3 分鐘的兵力與防禦價值，p=0.5 與 p=0.8 兩條分位數曲線",
  });
  const w80 = Q.waves["80"][0], w50 = Q.waves["50"][0];
  const calls = [
    [`${Q.games} 場`, "對困難 AI 的實機紀錄，記憶體裡雙方每個單位都看得到（沒有戰爭迷霧）"],
    [`第 ${w80.minute} 分`, `第一波反攻：p=0.5 約 $${w50.value.toLocaleString()}，p=0.8 約 $${w80.value.toLocaleString()}——家裡至少要擋得住這個`],
    ["p 越高越保守", "每個時段取「排第 ⌈N·p⌉ 名」那一場，跟論文式 (6) 一樣"],
  ];
  calls.forEach(([big, b], i) => {
    const y = 1.3 + i * 1.18;
    text(s, big, { x: 6.45, y, w: 3.05, h: 0.5, fontSize: 22, bold: true, color: C.accent2 });
    text(s, b, { x: 6.45, y: y + 0.5, w: 3.05, h: 0.62, fontSize: 11, color: C.text2 });
  });
  source(s, `資料：${Q.games} 場對困難 AI 的實機記憶體紀錄（其中 8 場是只守家的觀察局）；曲線是每個時段的平均價值`);
  s.addNotes(`論文的第一招是用資料學門檻 ξ，我們真的做了。論文的對手是真人，我們的對手是困難 AI，所以資料就是我們跟困難 AI 打的實機紀錄，而且我們讀記憶體，沒有戰爭迷霧，AI 每個單位都看得到。做法跟論文一樣：每三分鐘一個時段，把每場的 AI 兵力排名，取第 N 乘 p 名。藍線是 p 等於 0.5，紅線是 0.8，越上面越保守。中後段兩條線分得最開，代表困難 AI 每場的打法差異主要在中期，例如有幾場會多蓋光稜塔、早一點派兵反攻。p 等於 0.8 的版本，反攻波也比較早、比較多。反攻也一樣：第一波大約在第 ${w80.minute} 分鐘，家裡的防守至少要擋得住這個強度。`);
}
{
  const A = Q.ablation, c = Q.chosen;
  const range = g => g.length ? `${Math.min(...g)}–${Math.max(...g)}` : "-";
  const helps = A.topk.solved > A.random.solved || A.topk.sd < 0.6 * A.random.sd;
  const s = content("用在紅色警戒", helps ? "照論文做②：好方案當起點＋多目標搜尋" : "照論文做②：好方案當起點，效果有限");
  const col = (x, title, d, accent) => {
    card(s, x, 1.3, 2.75, 2.1, `ablation ${title}`);
    text(s, title, { x: x + 0.2, y: 1.38, w: 2.35, h: 0.35, fontSize: 13, bold: true, color: accent });
    text(s, `${d.solved}/${d.runs} 次`, { x: x + 0.2, y: 1.75, w: 2.35, h: 0.42, fontSize: 21, bold: true, color: accent });
    text(s, `找到打得贏 p=0.8 的方案；第一次在第 ${range(d.first_gen)} 代`, { x: x + 0.2, y: 2.17, w: 2.35, h: 0.42, fontSize: 10, color: C.text2 });
    text(s, `±${d.sd} 幀`, { x: x + 0.2, y: 2.62, w: 2.35, h: 0.38, fontSize: 18, bold: true, color: accent });
    text(s, `獲勝時間的標準差（平均 ${d.mean.toLocaleString()} 幀）`, { x: x + 0.2, y: 3.0, w: 2.35, h: 0.32, fontSize: 10, color: C.text2 });
  };
  col(0.5, "全部隨機", A.random, C.accent3);
  col(3.4, `前 ${A.top_k} 個好方案當起點`, A.topk, C.accent2);
  text(s, helps ? `同樣搜尋 20 代、各跑 ${A.random.runs} 次：跟論文表 3 一樣，有好起點就更穩`
                : `同樣搜尋 20 代、各跑 ${A.random.runs} 次：最後結果差不多。我們只選「兵種×數量」，搜尋空間比論文的 200 步建造順序小很多，隨機也找得到`,
       { x: 0.5, y: 3.45, w: 5.65, h: 0.55, fontSize: 11, bold: true, color: C.text1 });
  card(s, 6.45, 1.3, 3.05, 2.65, "chosen plan");
  text(s, "多目標前緣選出的方案", { x: 6.65, y: 1.4, w: 2.7, h: 0.35, fontSize: 13, bold: true, color: C.accent2 });
  const army = Object.entries(c.army).map(([t, n]) => `${UNIT[t] || t} ×${n}`).join("、");
  text(s, army, { x: 6.65, y: 1.8, w: 2.7, h: 0.75, fontSize: 13, bold: true });
  text(s, [
    { text: `花費 $${c.spent.toLocaleString()}`, options: { breakLine: true } },
    { text: `對 p=0.8 約第 ${(c.victory.hard_q80 / 1200).toFixed(1)} 分鐘獲勝`, options: { breakLine: true } },
    { text: `部隊剩 ${c.survive}%；前緣共 ${c.front} 種方案` },
  ], { x: 6.65, y: 2.6, w: 2.7, h: 1.25, fontSize: 11, color: C.text2, paraSpaceAfter: 4 });
  card(s, 0.5, 4.1, 9.0, 0.7, "objectives");
  text(s, "四個目標一起比：打贏 p=0.5 的時間、打贏 p=0.8 的時間、花費、部隊剩多少。打輸用懲罰函數扣分，分得出差一點和差很多。", { x: 0.7, y: 4.13, w: 8.6, h: 0.64, fontSize: 12, color: C.text1, valign: "middle" });
  source(s, `模擬結果（對手：${Q.games} 場困難 AI 實機紀錄的分位數）；方案還沒實機驗證（tools/topk_ablation.py）`);
  s.addNotes(`第二招是用好方案當起點。論文用職業選手的建造順序，我們用以前搜尋找到的方案和實機打贏過的方案，挑最好的 ${A.top_k} 個放進初始族群。我們照論文表三做了對照實驗：同樣搜尋 20 代，各跑 ${A.random.runs} 次。全部隨機的版本 ${A.random.runs} 次裡有 ${A.random.solved} 次找到打得贏的方案，標準差正負 ${A.random.sd} 幀；有好起點的版本 ${A.topk.solved} 次找到，標準差正負 ${A.topk.sd} 幀。` +
    (helps ? "跟論文的發現一樣，有好起點更穩。"
           : "結果是差不多，這跟論文不一樣。原因是我們搜尋的是兵種和數量，空間比論文 200 步的建造順序小很多，隨機起點也很快找得到；好起點只讓「第幾代第一次找到解」比較集中。這也說明論文這招在搜尋空間大的時候才特別有用。") +
    `另外我們同時看四個目標：打贏兩種強度 AI 的時間、花費和剩下多少部隊，最後從前緣選出 ${army}，模擬大約 ${(c.victory.hard_q80 / 1200).toFixed(0)} 分鐘打贏，這個方案下一步要實機驗證。`);
}

// ================================================================ demo video: easy rush
{
  const s = content("用在紅色警戒", "示範影片：快攻打簡單 AI（56 秒）");
  const V = "D:/RedAlert_Automation/thesis/videos/";
  s.addShape(pres.shapes.RECTANGLE, { x: 0.42, y: 1.22, w: 6.56, h: 3.71, fill: { color: C.text1 }, line: { color: C.text1 }, shadow: shadow(), objectName: "rush video frame" });
  s.addMedia({ type: "video", path: V + "rush_easy.mp4", cover: "data:image/png;base64," + require("fs").readFileSync(V + "rush_cover.png").toString("base64"), x: 0.5, y: 1.3, w: 6.4, h: 3.6, objectName: "rush video" });
  const steps = [["1", "自己開遊戲", "點選單、進遭遇戰"], ["2", "照模型建造", "預測 2067 幀，實際 1986 幀"], ["3", "集結", "38 個單位到齊才進攻"], ["4", "拆建造場", "先打威脅，再拆建造場"]];
  steps.forEach(([k, h, b], i) => {
    const y = 1.25 + i * 0.8;
    badge(s, 7.2, y + 0.05, k, `rush step ${k}`, 0.36);
    text(s, h, { x: 7.7, y, w: 1.8, h: 0.3, fontSize: 13, bold: true });
    text(s, b, { x: 7.7, y: y + 0.3, w: 1.8, h: 0.45, fontSize: 11, color: C.text2 });
  });
  text(s, "第 6724 幀（約 5.6 分鐘）獲勝，模擬預測誤差 +5.9%", { x: 7.2, y: 4.45, w: 2.3, h: 0.5, fontSize: 11, bold: true, color: C.accent2 });
  source(s, "影片：實機自動對戰錄影（遊戲畫面＋程式解說），加速播放");
  s.addNotes("這是一場完整的實機對戰，加速成一分鐘內。第一段，程式自己開遊戲、點選單進遭遇戰，完全不用碰滑鼠。第二段，訓練好的模型即時決定建造順序，模擬預測第 2067 幀蓋好，實際第 1986 幀，誤差 4% 左右。第三段，全軍出發，先在敵方基地外面集合，等到齊才一起進攻，這是之前輸了好幾場學到的教訓。第四段，先打會反擊的單位和碉堡，再拆建造場，讓 AI 沒辦法重建。最後在第 6724 幀，大約五分半鐘擊敗 AI，跟模擬預測只差 6%。");
}

// ================================================================ video D
{
  const s = content("用在紅色警戒", "戰鬥回放：這一場為什麼會輸");
  const V = "D:/RedAlert_Automation/thesis/videos/";
  s.addShape(pres.shapes.RECTANGLE, { x: 0.42, y: 1.22, w: 6.56, h: 3.71, fill: { color: C.text1 }, line: { color: C.text1 }, shadow: shadow(), objectName: "video frame" });
  s.addMedia({ type: "video", path: V + "D_battle_replay.mp4", cover: "data:image/png;base64," + require("fs").readFileSync(V + "D_cover.png").toString("base64"), x: 0.5, y: 1.3, w: 6.4, h: 3.6, objectName: "battle replay video" });
  const pts = [
    ["集結有效", "29 個單位幾乎同時抵達敵方基地"],
    ["但還是輸", "AI 一邊打一邊從工廠補兵，紅線掉得比藍線快"],
    ["後來的修正", "改選目標規則、加守家部隊，並把「AI 補兵速度」加進模擬"],
  ];
  pts.forEach(([h, b], i) => {
    const y = 1.3 + i * 1.2;
    text(s, h, { x: 7.25, y, w: 2.25, h: 0.36, fontSize: 15, bold: true, color: C.accent2 });
    text(s, b, { x: 7.25, y: y + 0.38, w: 2.25, h: 0.75, fontSize: 12, color: C.text2 });
  });
  source(s, "影片：由實機記憶體紀錄（logs/battle_medium_rush2.jsonl）自動產生；紅＝我方、藍＝AI");
  s.addNotes("這是用實機錄下來的資料自動畫出來的戰鬥回放，紅色是我們，藍色是 AI。可以看到部隊先在敵方基地前集合，然後一起衝進去，集結這部分是有效的。但右下角的部隊數量可以看到，紅線掉得比藍線快，因為 AI 會一邊打一邊從工廠補兵。這場輸了之後，我們改了選目標的規則、加了守家部隊，也把 AI 的補兵速度加進模擬器，模擬的預測才跟實際對得上。這也呼應論文的精神：參數要從資料學，而不是自己猜。");
}

// ================================================================ 17. next steps
{
  const s = content("用在紅色警戒", "接下來怎麼做");
  const steps = [
    ["實機挑戰", "用前緣選出的方案實機打困難 AI，驗證模擬的預測"],
    ["線上更新", "每打一場就把紀錄加進分位數，門檻越來越準——論文沒做到的部分"],
    ["錄示範", "我自己打幾場遭遇戰，程式只讀記憶體記錄決策 → 真人的「高手資料」"],
  ];
  steps.forEach(([h, b], i) => {
    const x = 0.5 + i * 3.05;
    card(s, x, 1.3, 2.85, 2.25, `next ${i + 1}`);
    badge(s, x + 0.25, 1.5, i + 1, `next badge ${i + 1}`);
    text(s, h, { x: x + 0.85, y: 1.5, w: 1.85, h: 0.42, fontSize: 17, bold: true, valign: "middle" });
    text(s, b, { x: x + 0.25, y: 2.1, w: 2.4, h: 1.35, fontSize: 13, color: C.text2 });
    if (i < steps.length - 1) s.addShape(pres.shapes.RIGHT_ARROW, { x: x + 2.88, y: 2.3, w: 0.14, h: 0.26, fill: { color: C.accent1 }, line: { color: C.accent1 }, objectName: `next arrow ${i + 1}` });
  });
  card(s, 0.5, 3.85, 9.0, 0.95, "closing");
  text(s, "這篇的核心精神：讓資料決定參數，而不是靠人拍腦袋。", { x: 0.75, y: 3.95, w: 8.5, h: 0.4, fontSize: 16, bold: true });
  text(s, "分位數門檻（11 場資料）和好方案起點都已接進多目標搜尋，接下來是實機驗證。", { x: 0.75, y: 4.35, w: 8.5, h: 0.38, fontSize: 13, color: C.text2 });
  s.addNotes("最後是下一步。第一，用前緣選出的方案實機挑戰困難 AI，驗證模擬的預測。第二，每打一場就把紀錄加進分位數，門檻會越來越準，這是論文沒有做到的線上更新。第三，我自己打幾場遭遇戰，程式只讀記憶體，把每個決策記下來，當成真人的高手資料。總結一句話：這篇論文的核心精神是讓資料決定參數，而不是靠人拍腦袋，這也是我們接下來要補強的地方。謝謝大家。");
}

(async () => {
  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("wrote", OUT);
})();
