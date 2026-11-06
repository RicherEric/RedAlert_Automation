# RedAlert_Automation

《紅色警戒 2：尤里的復仇》的策略最佳化工具組：從遊戲檔解出規則數據，用戰鬥模擬和經濟模擬，
在多重約束下找最佳兵種組合和建造順序。

## 安裝

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe extract.py      # 從 MIX 解出 rulesmd.ini 等檔 -> data/
```

## 使用

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py ra2opt.py units Russians                          # 列出某國可用的作戰單位 ID
& $py ra2opt.py army  scenarios\soviet_vs_allied.toml   # 兵種組合最佳化
& $py ra2opt.py build scenarios\soviet_vs_allied.toml   # 建造順序最佳化
& $py ra2opt.py plan  scenarios\soviet_vs_allied.toml   # 先找兵種，再找它的建造順序
& $py ra2opt.py pareto scenarios\soviet_vs_allied.toml --prefer win=2 frames=1   # 多目標：勝差/時間/花費的 Pareto 前緣
& $py -m pytest -q tests                                # 測試
```

結果會存到 `results/*.json`。情境檔的所有欄位都寫在 `scenarios/soviet_vs_allied.toml` 的註解裡。

## 約束條件

| 問題 | 約束 |
|---|---|
| 兵種 `[army]` | `budget`、`max_units`/`min_units`、`max_per_unit`/`min_per_unit`、`min_aa_units`、`min_score`（每種敵情都要贏）、`deadline_frames`（要在時限內蓋得出來，會呼叫經濟模擬）、`aggregate`（mean/worst） |
| 建造 `[build]` | `deadline_frames`、`max_count`、`milestones`（某建築要在某幀前完成）、`max_spent`、`max_low_power_frames` |

約束用 Deb 可行性規則處理：可行解一定勝過不可行解；兩個都不可行時，比較違反量的總和。
所以可以疊加任意多條約束，不用手調懲罰權重。

## 訓練即時決策模型（GA 當老師 → 模仿學習）

GA 算一次建造順序要好幾秒，對戰中來不及隨局面重算。所以先離線用 GA 大量出題，
再把它的決策訓練成一個模型：對戰時每次決策只要約 1 ms。

```powershell
& $py datagen.py --minutes 30 --workers 14   # 第 2 階段：GA 老師標註 → datasets/build/*.npz
& $py train.py                               # 第 3 階段：訓練策略與價值模型 → models/build/，並和老師對打評估
& $py train.py --eval-only --tasks 30
& $py dagger.py --rounds 3 --minutes 15      # 第 4 階段：DAgger，學生下棋、老師標註 → 重新訓練 → 評估
```

- **第 1 階段 `ra2tools/env.py`**：`BuildEnv` 把經濟模擬變成一步一步的決策環境。
  - 某條生產佇列空了、而且有合法選項時，就要做一次決策：生產哪一項，或等待。
  - 動作遮罩來自遊戲規則（前置條件、工廠、建造上限）。
  - 照固定順序走時，結果和 `EconomySim.run` 逐幀相同。
  - `state_from_snapshot` 可以從即時記憶體建立同樣的局面。
- **第 2 階段 `ra2tools/teacher.py`**：每個決策點都從當下局面跑 GA。
  - 目標是時間和花費的加權組合，每局隨機抽權重，為多目標做準備。
  - 15% 的步驟走隨機動作，讓資料也涵蓋「走錯之後怎麼救」的局面。
  - 另外記錄老師計畫預測的完成幀，作為價值模型的標籤。
- **第 3 階段 `train.py`、`ra2tools/policy.py`**：
  - LightGBM 策略模型，輸入特徵含目標權重，推論時套上合法遮罩。
  - 另有預測完成幀的價值模型。
  - 評估方式是在新任務上，讓學生和老師各自完整下一局，比較加權目標和決策時間。
  - 評估平行執行；老師的對局結果快取在 `datasets/teacher_eval.json`，之後每輪評估只要跑學生。
- **第 4 階段 `dagger.py`**：只學老師走過的局面，學生一走偏就不知道怎麼辦，錯誤會累積
  （一直等待、多蓋不需要的東西）。
  - 每一輪由目前的學生下棋（以機率 β 改走老師的動作，β 每輪減半），
    老師標註學生走到的每個局面。
  - 新資料加進資料集後，用全部資料重新訓練，再用同一組固定任務評估。
  - 每輪結果記錄在 `models/build/dagger.json`。
- **特徵與安全網**：
  - 特徵包含「剩下目標的建造路徑」：用 `resolve()` 算出還缺哪些前置建築，例如防空步兵需要雷達。
    這樣罕見目標的前置鏈也看得到。
  - 特徵改版時會調高 `FEATURE_VERSION`。資料和模型的版本對不上時會直接報錯；
    舊資料要先搬走（例如 `datasets/build_v1`）。
  - 安全網：整個基地閒置超過 90 幀、學生還選等待時，改蓋前置鏈上分數最高的合法項目，
    保證不會永遠卡住。評估時會統計介入次數。
  - 第 3 版特徵拿掉「已經過的幀數」和「已花的錢」。最佳的下一步只取決於目前局面，
    但訓練局都從同樣條件開始，樹模型就拿幀數當捷徑。實機比模擬快 40 幀時，
    它在同一個局面選了不同的建築（多蓋一座礦場，慢 12%）。
    另外加上「剩下路徑的總價」和「錢還差多少」，判斷蓋第二座工廠划不划算。
    價值模型改成預測剩餘幀數。

## 多目標：要蓋什麼部隊（第 5 階段）

`ra2opt.py pareto` 用 NSGA-II（`ra2tools/optimize.py` 的 `run_nsga2`）同時最佳化三個目標：

- 勝差：對 `[army]` 裡每一種敵情的戰鬥模擬結果。
- 整支部隊完成的幀數：由訓練好的建造模型規劃，比 `resolve()` 的預設順序快 7–42%；
  兩者取較快的，所以模型遇到沒見過的部隊也不會變差。
- 總花費：部隊加上需要的建築。

結果是一條 Pareto 前緣：每一種組合都不會在三個目標上同時輸給另一種。
`[pareto] preference`（或 `--prefer win=2 frames=1 spent=0`）用 Tchebycheff 從前緣挑一個。
和加權總和不同，它也挑得到前緣凹進去的部分。
最後印出建造順序和 `execute.py --targets ...` 指令，可以直接拿去實機執行。
`[pareto]` 的鍵會覆寫 `[army]`；`ignore = ["budget", "deadline_frames"]` 讓錢和時間當目標，而不是約束。

## 破關：最快打贏整場（第 6 階段）

```powershell
& $py opponent.py extract logs\game3_soviet.jsonl --name easy_americans_firestorm   # AI 隨時間的部隊與建築
& $py ra2opt.py victory scenarios\soviet_vs_allied.toml                            # 破關 Pareto 前緣
& $py execute.py --attack --targets E2=20 SHK=2                                     # 實機：蓋完就全軍進攻
```

- `ra2tools/opponent.py`：從實機記錄（`monitor.py watch --log-objects`）抽出 AI 每個時間點的
  作戰部隊和建築、兩邊基地的距離，以及我方開局送的部隊。
  簡單 AI 幾乎不擴軍（部隊價值一直在 $4–6k），但會一直蓋基地；光稜塔大約第 7500 幀才出現，之後越來越多。
- `VictoryProblem`（`ra2tools/problems.py`）：部隊蓋好就連同開局部隊出發。
  行軍時間依最慢的單位計算（地面部隊乘上 `travel_factor`）。
  抵達時和 AI 當下的部隊加上整個基地交戰：建築在戰鬥模擬裡是不會動的單位，`ShortGame` 下拆光就贏。
  - 目標：最早獲勝、花費最少、剩下最多。
  - 打不贏算違反約束，違反量是 AI 剩下多少，用來引導搜尋。
- `ra2bridge.dll` 新增 `ATTACK <我方單位> <目標位址>`（Mission::Attack 事件）。
  YR 1.001 沒有攻擊移動，所以 `ra2tools/assault.py` 幫每個單位挑目標：
  附近有會反擊的敵人就先打它，否則打最近的建築；目標死了就換下一個。
- `execute.py --attack` 蓋完 `--targets` 的部隊後開始進攻，直到 AI 被擊敗。
  會印出預測和實際的出發、獲勝幀數。

## 讀取即時遊戲狀態（唯讀）

```powershell
& $py monitor.py check     # 進入遭遇戰後執行：檢查記憶體位址是否符合你的 gamemd.exe
& $py monitor.py watch --log logs\game1.jsonl   # 即時顯示金錢、電力、生產進度、單位，並記錄日誌
```

### 控制遊戲（B 階段：ra2bridge.dll）

```powershell
tools\ra2bridge\build.bat                  # 用 VS 2022 編 32 位元 DLL 和注入器（第一次會 clone YRpp）
& $py control.py ping                      # 注入（如果還沒）並測試連線
& $py control.py deploy SMCV               # 展開基地車（種類 ID 或記憶體位址 0x...）
& $py control.py build NAPOWR              # 生產 → 等完成 → 自動找位置擺放
& $py control.py produce HTNK ; & $py control.py move HTNK 40 50
```

**自動執行建造順序**：

```powershell
& $py execute.py results\soviet_vs_allied_plan_....json    # 照最佳化結果蓋，並和模擬預測比對
& $py execute.py --order NAPOWR NAREFN NAWEAP HTNK HTNK     # 或直接指定順序
& $py execute.py --targets APOC=2 --weight 1.0              # 不給順序：訓練好的模型即時決策
```

`--targets` 模式用 `ra2tools/live_policy.py`：哪條生產佇列空了，就從記憶體快照建立局面
（`state_from_snapshot`），當場問模型要蓋什麼。預測是同一個模型在模擬器裡下的結果。
每次決策的輸入（資金、持有數量、工廠狀態）都會記錄下來，用來追查實機和模擬不一樣的決策。
遭遇戰開局送的部隊（例如 4 台犀牛坦克、12 個動員兵）不算進模型的局面，也不算達成目標，
因為模擬的每一局都只從基地車開始。

`ra2tools/executor.py` 跟經濟模擬一樣，每個生產類別一條佇列依序生產。建築完成就自動擺放；
從記憶體看到地圖上多一個，就算完成。結束時會印出每一項的「預測 vs 實際」幀數，
記錄存到 `logs/execute_*.json`。預測用 bot 的延遲（`bot_place_delay_frames`、
`bot_mcv_deploy_frames`），不用人手量到的值，因為人手的值包含反應時間。如果最佳化的結果要交給 bot 執行，
就在情境檔的 `[calibration]` 加上 `controller = "bot"`。

**基地管理與即時應變（2026-11-06）**：`--targets` 模式另外跑三個控制器。
- `ra2tools/steward.py`（`Steward`）：
  - 維修：受損建築依重要性排序，同時最多修 2 座，留 $400 給生產。
  - 工兵：去佔最近的中立科技油井，再來是機場。
  - 礦車：停住超過 450 幀、又不在礦場旁邊，就派去「每包價值 ÷ 距離」最高的礦區（彩色礦優先），
    礦區用 `ra2tools/mapdata.py` 從地圖檔的 `[OverlayPack]` 解出來。
  - 進駐：派開局動員兵進離基地最近的民房，守家礦和油井。
  - 重蓋：蓋兵階段結束後，被打掉的電廠、礦場、工廠會重蓋。
- `ra2tools/adapt.py`：
  - 蓋兵時每 3000 幀，拿記憶體看到的 AI 部隊和計畫部隊做戰鬥模擬。打不贏就追加剋制兵種：
    每一輪試每個候選兵種，挑「每花一塊錢分數進步最多」的（`--adapt`，最多 $5000）。
  - 出發後，多的錢拿去蓋剋制兵種留在家守（`Reinforcer`）。
  - 記憶體看得到整張地圖，只能用在打電腦。
- `ra2tools/assault.py` 集火：每個單位在射程內的威脅中，挑「加上隊友已經打在它身上的火力後，
  每幀打掉最多敵方價值」的目標；隊友已經 45 幀內就能打死的目標，分數打四分之一，避免過度集火。
  戰鬥模擬（`[combat] focus = true`）用同一種選法。

bridge 為此新增 `REPAIR`、`CAPTURE`（工兵佔領，也用來進駐民房）、`ENTER`、`HARVEST`。
實測：步兵進駐民房要用 Capture 任務，用 Enter 的話，動員兵會把民房打掉。

DLL 在 `MainLoop`（0x55D360）開頭掛一個 5 位元組的跳轉，每幀在遊戲主執行緒上處理指令。
玩家動作都轉成遊戲自己的 `EventClass` 事件（跟點側邊欄一樣），由遊戲驗證和排程。
Python 透過 `127.0.0.1:47800` 的文字協定溝通（`ra2tools/bridge.py`）。網際網路對戰模式下一律拒絕執行。

### 自動開遊戲並進入遭遇戰（測試用）

```powershell
& $py autostart.py start   # 備份 RA2MD.INI → 套用固定測試設定 → 視窗模式啟動 → 點選單進入遭遇戰
& $py autostart.py stop    # 關閉遊戲並還原你的 RA2MD.INI
```

固定設定寫在 `autostart.py` 的 `PROFILE`（800×600、速度 3、蘇俄、1 個電腦、資金 10000）。
遊戲用 `-WIN` 視窗模式執行：失去焦點也不會暫停，選單按鈕位置固定。每一步都會先截圖比對
`tools/autostart_refs/` 的選單標題才點擊，畫面不對就停下來。點擊時約 15 秒內不要動滑鼠鍵盤。

位址和偏移量在 `ra2tools/yr_layout.py`，來源是 YRpp 標頭檔，再經 MSVC 排出的類別佈局
（`tools/yr_layout/dump_layout.bat`）。只會讀取，不會寫入遊戲記憶體。
**只能用在遭遇戰或經朋友同意的區網對戰，不要拿到線上對戰。**

## 結構

- `ra2tools/mix.py`：MIX 解包（含 Blowfish 加密標頭、巢狀 MIX）
- `ra2tools/ini.py`、`csf.py`：INI / 字串表解析
- `ra2tools/gamedata.py`：單位、武器、前置條件、國家可用性
- `ra2tools/combat.py`：戰鬥模擬（numpy 向量化）
- `ra2tools/economy.py`：經濟／建造模擬
- `ra2tools/optimize.py`：帶約束的遺傳演算法（可用 `workers` 平行化）
- `ra2tools/problems.py`：兵種最佳化、建造順序最佳化的問題定義
- `ra2tools/params.py`：**校準參數**（以下說明）
- `ra2tools/memory.py`、`yr_layout.py`：讀取 gamemd.exe 記憶體
- `ra2tools/calibration.py`：校準擬合（碼錶量測、watch 日誌）
- `ra2tools/mapdata.py`：地圖檔的礦區／彩色礦（`[OverlayPack]`，LCW 解壓）
- `ra2tools/steward.py`、`adapt.py`：實機的基地管理（維修、油井、礦車、進駐、重蓋）和依敵軍追加兵種

## 模型範圍與校準

時間一律以「遊戲幀」計算；`ROF`、建造時間等規則數值本來就是以幀為單位。

**照規則檔計算（不需校準）**：價格、血量、護甲倍率、射程、射速、連發、濺射、
對空/對地、建造時間公式（`BuildSpeed`、`MultipleFactory`、低電力懲罰）、前置條件、礦車容量、
科技油井收入（佔領時 `ProduceCashStartup`，之後每 `ProduceCashDelay` 幀 `ProduceCashAmount`）。

**從地圖或實機量（2026-11-06）**：油井距離 `derricks`（工兵走過去的格數）；
AI 天氣控制器的閃電風暴（`VictoryProblem.storms`：蓋好約 12500 幀後第一擊，之後每 9000 幀，
每擊延後我方完成時間＝重蓋戰車工廠＋礦場）。維修和進駐只在實機做，模擬沒有算它們的好處。

**需要用實戰校準的假設**（`[calibration]`）：`fps`（每秒幾幀，依遊戲速度而定）、
`speed_to_cells`（`Speed=` 換算成每幀幾格）、`ore_distance`、採礦/卸礦時間、奴隸礦工循環、
擺放建築的延遲、飛機往返補彈時間。

### 校準流程

```powershell
& $py calibrate.py tests                                      # 每項量測的做法
& $py calibrate.py record build --item NAPOWR --speed 6       # 遊戲中按 F9 標記、F8 取消、F10 結束
& $py calibrate.py record move --unit HTNK --cells 10
& $py calibrate.py record harvest --unit HARV
& $py calibrate.py add build --item NAREFN --seconds 21.0     # 也可以手動輸入碼錶秒數
& $py calibrate.py list ; & $py calibrate.py drop 2           # 檢視 / 刪除量測
& $py calibrate.py fit                                        # -> calibration/calibration.toml
```

**自動校準（推薦）**：開遊戲前在背景跑
`monitor.py watch --log logs\game1.jsonl --log-objects --interval 0.5 --quiet --duration 900`
（單人模式切到別的視窗會暫停，所以不要邊看邊切），結束後執行 `calibrate.py auto logs\game1.jsonl`：

- fps：遊戲幀計數器 ÷ 實際秒數（排除暫停），依遊戲速度設定分開記錄到 `[fps_by_speed]`。
  `ra2opt.py` 執行時如果遊戲開著，會自動讀目前的速度設定並套用對應的 fps；
  也可以在情境檔 `[calibration]` 寫 `game_speed = 3` 指定。`monitor.py watch` 會即時顯示滾動 5 秒的實測 fps
- 建造時間：工廠進度條，和規則公式比對（含多座工廠、排除低電力）
- `speed_to_cells`：單位直線等速移動的片段
- 採礦：礦車軌跡切成「出發 → 採礦 → 回程 → 卸礦」
- `place_delay_frames`：建築完成到出現在地圖上；`mcv_deploy_frames`：日誌要從第 0 幀開始

已驗證的引擎行為：進度條 54 格，每格幀數無條件捨去成整數，多座工廠是先乘 `MultipleFactory` 再捨去。

`build` 是基準：建造時間的幀數可以從規則檔精確算出，用它反推 fps，其他量測再用 fps 換算成幀。
fps 取決於遊戲速度，請用你平常玩的速度量。參數優先順序：預設值 < `calibration/calibration.toml` < 情境檔的 `[calibration]`。

**戰鬥模擬有模型化的部分**：輾壓步兵、部署武器（大兵、重裝大兵、武裝直升機降落後變成地面單位）、
飛機彈藥與補彈、自殺武器、`kite`（快速且射程較遠的單位邊退邊打）、`spacing`（陣型間距）。

**沒有模型化的部分**：地形、尋路、碰撞、升級（老兵）、彈道飛行時間、心靈控制、超時空、
恐怖機器人、磁電坦克抓取、V3/航母子機、C4 炸藥。用到這些武器的單位會自動排除。

**注意**：最佳化器會鑽敵情設定的漏洞。例如所有敵情都沒有防空時，它就會選全空軍。
敵情 `[[army.enemy]]` 要盡量涵蓋對手的實際反制手段。
