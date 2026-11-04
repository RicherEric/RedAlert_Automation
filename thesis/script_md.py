"""Build the 使用方式 + 逐頁講稿 markdown from the deck's speaker notes (notes.json)."""
import json
import re
import sys
from pathlib import Path

notes = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
# One key sentence per slide, bolded in the script (must be a substring of that slide's notes).
KEY = {
    1: "這篇論文其實只回答兩個問題：兵要出多少、從哪裡開始找，答案都是看資料",
    2: "兵到底要出多少才夠",
    3: "問題是 ξ 要設多少",
    4: "ξ 是及格線，不是答案",
    5: "論文說 N 是用來懲罰兵量不足的",
    6: "我的兵量要不少於 p 比例的對手",
    7: "攻城坦克從 0 一路漲到 12.43 台",
    8: "論文沒有寫為什麼選基因演算法",
    9: "能用的解東一塊、西一塊，彼此連不起來",
    10: "作者把高手錄影裡的建造順序排序後，取前 K 個放進初始族群",
    11: "第零代就在負一百到負三百之間",
    12: "放進一個高手開局就到 240 分",
    13: "我要找的，就是每種單位各蓋幾個、先蓋哪些",
    14: "大約五分半鐘打贏簡單 AI",
    15: "怎麼把這套方法搬過去、能不能打贏最難的困難 AI，期末再跟大家報告",
}
TITLES = {1: "標題：兩個問題", 2: "什麼是 Build-order", 3: "到底要出多少兵？",
          4: "整體架構：資料先算好，演算法再找答案", 5: "先改寫題目：懲罰函數", 6: "地圖 ①：ξ 怎麼從錄影學出來",
          7: "地圖 ①：學出來的門檻", 8: "怎麼解：改良版基因演算法", 9: "改良 ①：模擬退火",
          10: "改良 ②：高手開局（TopK）", 11: "結果：加入高手開局",
          12: "加入高手開局，更好也更穩", 13: "紅色警戒 2：會用到的單位", 14: "紅色警戒 2：目前能自動打贏簡單 AI（影片，邊播邊講）",
          15: "總結與期末預告"}
# 5-minute cut: page -> (seconds, what to keep)
FIVE_MIN = {2: (15, "只講煮菜比喻和最後的問句"), 4: (25, "只講左右兩半和「ξ 是及格線，不是答案」"), 3: (20, "只講「24 格加起來，越多越好」、式三的坦克例子和「ξ 要設多少」"),
            5: (30, "只講「ξ 定不準」、式四、表上 11 台和 2 台、N 那句"),
            6: (30, "p = 0.8 的例子只講「ξ 更大、更保守、論文沒實驗」"),
            7: (20, "只講「不是分數，是看到幾隻」和攻城坦克那句"),
            8: (30, "只講染色體、模擬器、食譜比賽一句和粗體句"),
            9: (25, "只講原話、局部搜尋、山的圖和「論文沒有證明」"),
            10: (20, "只講取前 K 個那句和「不是請高手出意見」"),
            11: (25, "只講怎麼看和圖 2"), 12: (20, "只講兩個大數字和「放幾個差不多」"), 13: (15, "只講題目、天啟和最後兩句"), 15: (20, "只講「實際證明」「沒有驗證」和期末預告")}

rows, body, total, short = [], [], 0, 0
for i, title, text in notes:
    m = re.match(r"（約 (\d+) 秒[^）]*）", text)
    sec = int(m.group(1))
    total += sec
    short += FIVE_MIN.get(i, (sec,))[0]
    text = text[m.end():]
    key = KEY.get(i)
    if key and key not in text:
        print("KEY MISSING", i)
    if key and key in text:
        text = text.replace(key, f"**{key}**", 1)
    title = TITLES[i]
    rows.append(f"| {i} | {title} | {sec} 秒 | {total // 60}:{total % 60:02d} |")
    body.append(f"### 第 {i} 頁　{title}（{sec} 秒）\n\n{text}")

usage = [
    "## 使用方式",
    "",
    f"全場 {len(notes)} 頁、完整講約 {total // 60} 分 {total % 60:02d} 秒；要壓在 5 分鐘內，照下面「5 分鐘版」縮短。粗體是一定要講的句子。",
    "",
    "| 頁 | 內容 | 時間 | 累計 |",
    "| --- | --- | --- | --- |",
    *rows,
    "",
    f"**5 分鐘版（約 {short // 60} 分 {short % 60:02d} 秒）：**",
    "",
    *[f"- 第 {k} 頁：{v[0]} 秒：{v[1]}" for k, v in sorted(FIVE_MIN.items())],
    "",
    "- **影片：** 放映時點一下影片才會播；邊播邊講，影片播完剛好講完。",
    "- **練習：** 先照稿唸兩次計時，第三次只看粗體句子講。",
]
md = "\n".join(usage) + "\n\n## 逐頁講稿\n\n" + "\n\n".join(body) + "\n"
Path(sys.argv[2]).write_text(md, encoding="utf-8")
print(md[:600])
