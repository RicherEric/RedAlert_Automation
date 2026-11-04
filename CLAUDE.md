# RedAlert_Automation

紅色警戒 2：尤里的復仇（YR 1.001，Steam 版）的策略最佳化專案，加上碩士課程的期中報告。
**一律用繁體中文回覆。** Python 一律用 `.venv\Scripts\python.exe`。

## 目前重點：期中報告 PPT（`thesis/`）

5 分鐘報告：導讀 Wang et al., *A Data-driven Approach to Solve a Production Constrained Build-order
Optimization Problem*（CCC 2019, IEEE），最後用紅色警戒 2 賣關子。報告人：M1527488 柯義詮。
版面刻意簡單，像人做的；講稿是報告者自己的口吻。

| 檔案 | 用途 |
| --- | --- |
| `thesis/build_midterm.js` | 產生投影片的程式（pptxgenjs），**改投影片就改這個檔**，不要直接改 pptx |
| `thesis/期中報告_5分鐘.pptx` | 產出的投影片（13 頁，第 12 頁內嵌影片） |
| `thesis/期中報告講稿_5分鐘.md` | 講稿：整場主線、時間表、5 分鐘版刪減、逐頁講稿、可能被問的問題、名詞小抄 |
| `thesis/script_md.py` | 講稿 md 的生成規則：每頁粗體重點句 `KEY`、標題 `TITLES`、5 分鐘版 `FIVE_MIN` |
| `thesis/update_script.py` | 從 pptx 的備忘稿重建講稿 md 的「使用方式」和「逐頁講稿」兩段 |
| `thesis/images/`、`thesis/videos/` | 論文截圖（式子、表 1、表 3、圖 1、圖 2）、遊戲圖示、實機影片 |
| `thesis/NotebookLM_逐字稿.txt` | NotebookLM 對這篇論文的講解，可參考它的講法 |

論文 PDF 因授權不在 git 裡（學校從 IEEE Xplore 下載），要自己放到 `thesis/`。

### 改投影片的流程

```powershell
cd thesis; npm install                       # 第一次：裝 pptxgenjs 3.12.0
node build_midterm.js 期中報告_5分鐘.pptx       # 重建投影片（PowerPoint 要先關掉，不然檔案被鎖）
cd ..; .venv\Scripts\python.exe thesis\update_script.py   # 備忘稿改了就重建講稿 md
```

- `build_midterm.js` 需要 Claude Code pptx skill 的 `apply_theme.js` 來寫入主題色，會自動在 `~/.claude/skills` 底下找；
  找不到就設環境變數 `APPLY_THEME` 指到它。
- 每頁的講稿寫在 `s.addNotes("（約 N 秒）……")`，秒數會被 `script_md.py` 拿去算時間表。
  改了講稿要確認 `KEY` 的粗體句還在講稿裡，不然 `update_script.py` 會報 `KEY MISSING`。
- 改完要檢查：跑 pptx skill 的 `validate.py`，再轉成 PDF/圖片看每頁有沒有文字重疊或超出。
- 線上版講稿（Claude Docs）：https://claude.ai/artifact/PsuduQXMuahs52V5zRft5E ，md 改了要同步。
- `期中報告_5分鐘(無影片).pptx`、`論文導讀_資料驅動建造順序.pptx`、`build_deck.js`、`講稿_完整版.html` 是舊版，不用維護。

### 報告的結構（13 頁）

1 標題 → 2 什麼是 Build-order → 3 到底要出多少兵（式 1–3）→ **4 整體架構圖：資料定題目，演算法找答案**
→ 5–6 地圖 ①：ξ 從錄影學、學出來的門檻（表 1）→ 7 地圖評分格：懲罰函數 → 8 地圖 ②：TopK＋求解五步
→ 9 結果（圖 1、圖 2）→ 10 表 3（長度 200 的七列）→ 11 紅警單位 → 12 影片：自動打贏簡單 AI → 13 總結與期末預告。
第 5–10 頁右上角有「地圖：…」標籤對應到架構圖。紅警部分只秀成果，訓練方法留到期末。

### 已經跟使用者確認過的論文理解（講錯會被問倒）

- ξ 是**對手**的兵量門檻（及格線），不是答案；459 場依總兵量**由少到多**排，取第 N·p 名（p = 0.5，約第 230 名）。
  p 越大取越後面、兵越多，所以越保守；論文只做了 p = 0.5。表 1 的數字是「對手至少幾隻」，不是分數。
- 演算法要找的是建造順序（最長 200 步，一步 = 蓋一個東西）；ξ 算一次就固定，用在基因演算法每一代的評分。
- 硬限制是題目原本的寫法（式 3 必須滿足）；基因演算法靠分數挑方案，硬限制讓不合格的全部同分，
  所以作者用懲罰函數（式 4–5）改成扣分。
- 基因演算法三個弱點各補一招：硬限制下分數沒高低 → 懲罰函數；卡在小山頭 → 模擬退火；起點太差 → 高手開局 TopK。
  為什麼選基因演算法論文沒寫，是我們的理解（序列可直接當染色體、只能靠模擬器打分）。
- 模擬退火具體怎麼接進基因演算法論文沒寫，只引用文獻 [20]；153 代 = 溫度 1000 每代 ×0.9 降到 0.0001（我們推算的）。
- 「專家給 ξ」是主觀猜數字；高手開局是錄影裡實際打過、先評分再挑的建造順序，不矛盾。
- 實驗只證明 TopK 讓結果更好更穩（長度 200：36 → 240～253，標準差 ±182 → ±17～47）；
  ξ 是否比專家好、模擬退火有沒有用、真實遊戲有沒有用，論文都沒實驗。
- 每 12 幀記一次 ≈ 0.5 秒（母巢之戰 Fastest 約 24 fps，論文沒寫秒數）。

## 安全規則（實機相關，一定要遵守）

- 只能跟電腦 AI 打遭遇戰，或經朋友同意的區網對戰。**絕對不能上線上對戰／CnCNet。**
- 記憶體讀取（`monitor.py`、`ra2tools/memory.py`）只讀不寫。`ra2bridge.dll` 只聽 `127.0.0.1:47800`，網路對戰模式下拒絕執行。
- 改動前備份 `RA2MD.INI`，結束要還原（`autostart.py stop` 會做）。
- **每一次實機測試前都要先問使用者**，上一次的同意不算數。
- 只結束指定的 PID，不要用名稱整批砍程序。不要把遊戲視窗最小化（失去焦點會暫停）；啟動時約 15 秒內使用者不能動滑鼠鍵盤。
- 實機進行中不要跑吃 CPU 的工作。下載外部程式碼、建置或執行都要先取得使用者同意。

## 環境與換電腦要重建的東西

- 遊戲路徑預設 `D:\SteamLibrary\steamapps\common\Command & Conquer Red Alert II`（`ra2tools/launcher.py` 的 `DEFAULT_GAME_DIR`）。
- `python -m venv .venv` → `.venv\Scripts\pip install -r requirements.txt`。
- 不在 git 裡、要在新電腦重建的：
  - `data/*.ini`、`data/ra2md.csf`：遊戲著作權資料，跑 `extract.py "<遊戲路徑>"` 從 MIX 解出來。
  - `tools/third_party/`（Syringe、YRpp、yrpp-spawner）：`tools/build_spawner.bat`。
  - `tools/ra2bridge/out/`：`tools/ra2bridge/build.bat`（VS 2022，32 位元）。
  - `logs/`、`datasets/`、舊版模型：本機產物，沒有就算了。現行模型 `models/build` 有放在 git 裡。

## 研究進度（2026-11-04）

- 詳細用法與模型範圍見 `README.md`。核心：`ra2tools/`（經濟／戰鬥模擬、NSGA-II、記憶體讀取、bridge）、
  `ra2opt.py`（最佳化）、`execute.py`（實機執行）、`opponent.py`（AI 時間軸、分位數對手）。
- 簡單 AI：實機約 5.4 分鐘自動打贏，和模擬預測差約 6%。
- 困難 AI：還沒贏。robust 搜尋訓練勝率約 83%，leave-one-out 只剩 6/10；實機兩場都輸。
  主因是模擬太樂觀：家裡的礦約第 14000 幀採完、礦車要繞遠路，AI 一直騷擾，後期也比預測強。
- 期末方向：把這些補進模擬器，重新搜尋，再挑戰困難 AI（任何實機都要先問使用者）。
