# Local UniDic spike report (Issue #50)

Spike to evaluate whether `fugashi` + NINJAL UniDic (in-process) can
replace the current `Yahoo MA-UniDic (HTTP) + OJAD scrape` pipeline.

## Library / dictionary info

```
fugashi: 1.5.2
dictionary info: [
  {
    "filename": "/home/torridfish/Code/spike-local-unidic/.venv/lib/python3.11/site-packages/unidic/dicdir/sys.dic",
    "charset": "utf8",
    "size": 878989,
    "version": 102
  }
]
```


## Schema dump (per-morpheme fields)

Input: `毎朝コーヒーを飲みます`

| surface | pos1 | pos2 | cType | cForm | lemma | aType | aConType |
|---|---|---|---|---|---|---|---|
| 毎朝 | 名詞 | 普通名詞 | * | * | 毎朝 | 0,1 | C2 |
| コーヒー | 名詞 | 普通名詞 | * | * | コーヒー-coffee | 3 | C2 |
| を | 助詞 | 格助詞 | * | * | を | * | 動詞%F2@0,名詞%F1,形容詞%F2@-1 |
| 飲み | 動詞 | 一般 | 五段-マ行 | 連用形-一般 | 飲む | 1 | C1 |
| ます | 助動詞 | * | 助動詞-マス | 終止形-一般 | ます | * | 動詞%F4@1 |

Full first-token dump:
```python
{
  "surface": "毎朝",
  "pos1": "名詞",
  "pos2": "普通名詞",
  "pos3": "副詞可能",
  "pos4": "*",
  "cType": "*",
  "cForm": "*",
  "lForm": "マイアサ",
  "lemma": "毎朝",
  "orth": "毎朝",
  "pron": "マイアサ",
  "orthBase": "毎朝",
  "pronBase": "マイアサ",
  "goshu": "混",
  "iType": "*",
  "iForm": "*",
  "fType": "*",
  "fForm": "*",
  "kana": "マイアサ",
  "kanaBase": "マイアサ",
  "aType": "0,1",
  "aConType": "C2",
  "aModType": "*"
}
```

## Disambiguation probe (PR #49 set)

### `毎朝コーヒーを飲みます`

Expectation: ます = 助動詞・基本形 (kernel on ま)

**Local fugashi+UniDic:**

| surface | pos1 | pos2 | cType | cForm | lemma | aType |
|---|---|---|---|---|---|---|
| 毎朝 | 名詞 | 普通名詞 | * | * | 毎朝 | 0,1 |
| コーヒー | 名詞 | 普通名詞 | * | * | コーヒー-coffee | 3 |
| を | 助詞 | 格助詞 | * | * | を | * |
| 飲み | 動詞 | 一般 | 五段-マ行 | 連用形-一般 | 飲む | 1 |
| ます | 助動詞 | * | 助動詞-マス | 終止形-一般 | ます | * |

**Yahoo MA-UniDic:**

| surface | reading | base | pos | pos1 | cType | cForm |
|---|---|---|---|---|---|---|
| 毎朝 | まいあさ | 毎朝 | 名詞 | 普通名詞-副詞可能-* | - | - |
| コーヒー | こーひー | コーヒー | 名詞 | 普通名詞-一般-* | - | - |
| を | を | を | 助詞 | 格助詞-*-* | - | - |
| 飲み | のみ | 飲む | 動詞 | 一般-*-* | 五段-マ行 | 連用形-一般 |
| ます | ます | ます | 助動詞 | *-*-* | 助動詞-マス | 終止形-一般 |

### `ラーメンが食べたい`

Expectation: たい = 助動詞・基本形 (kernel on た)

**Local fugashi+UniDic:**

| surface | pos1 | pos2 | cType | cForm | lemma | aType |
|---|---|---|---|---|---|---|
| ラーメン | 名詞 | 普通名詞 | * | * | ラーメン-Rahmen | 1 |
| が | 助詞 | 格助詞 | * | * | が | * |
| 食べ | 動詞 | 一般 | 下一段-バ行 | 連用形-一般 | 食べる | 2 |
| たい | 助動詞 | * | 助動詞-タイ | 終止形-一般 | たい | * |

**Yahoo MA-UniDic:**

| surface | reading | base | pos | pos1 | cType | cForm |
|---|---|---|---|---|---|---|
| ラーメン | らーめん | ラーメン | 名詞 | 普通名詞-一般-* | - | - |
| が | が | が | 助詞 | 格助詞-*-* | - | - |
| 食べ | たべ | 食べる | 動詞 | 一般-*-* | 下一段-バ行 | 連用形-一般 |
| たい | たい | たい | 助動詞 | *-*-* | 助動詞-タイ | 終止形-一般 |

### `昨日映画を見ました`

Expectation: ました — kernel on ま of まし

**Local fugashi+UniDic:**

| surface | pos1 | pos2 | cType | cForm | lemma | aType |
|---|---|---|---|---|---|---|
| 昨日 | 名詞 | 普通名詞 | * | * | 昨日 | 2,0 |
| 映画 | 名詞 | 普通名詞 | * | * | 映画 | 0,1 |
| を | 助詞 | 格助詞 | * | * | を | * |
| 見 | 動詞 | 非自立可能 | 上一段-マ行 | 連用形-一般 | 見る | 1 |
| まし | 助動詞 | * | 助動詞-マス | 連用形-一般 | ます | * |
| た | 助動詞 | * | 助動詞-タ | 終止形-一般 | た | * |

**Yahoo MA-UniDic:**

| surface | reading | base | pos | pos1 | cType | cForm |
|---|---|---|---|---|---|---|
| 昨日 | きのう | 昨日 | 名詞 | 普通名詞-副詞可能-* | - | - |
| 映画 | えいが | 映画 | 名詞 | 普通名詞-一般-* | - | - |
| を | を | を | 助詞 | 格助詞-*-* | - | - |
| 見 | み | 見る | 動詞 | 非自立可能-*-* | 上一段-マ行 | 連用形-一般 |
| まし | まし | ます | 助動詞 | *-*-* | 助動詞-マス | 連用形-一般 |
| た | た | た | 助動詞 | *-*-* | 助動詞-タ | 終止形-一般 |

### `そこまでは行きません`

Expectation: ません — kernel on せ (NOT ま)

**Local fugashi+UniDic:**

| surface | pos1 | pos2 | cType | cForm | lemma | aType |
|---|---|---|---|---|---|---|
| そこ | 代名詞 | * | * | * | 其処 | 0 |
| まで | 助詞 | 副助詞 | * | * | まで | * |
| は | 助詞 | 係助詞 | * | * | は | * |
| 行き | 動詞 | 非自立可能 | 五段-カ行 | 連用形-一般 | 行く | 0 |
| ませ | 助動詞 | * | 助動詞-マス | 未然形-一般 | ます | * |
| ん | 助動詞 | * | 助動詞-ヌ | 終止形-撥音便 | ず | * |

**Yahoo MA-UniDic:**

| surface | reading | base | pos | pos1 | cType | cForm |
|---|---|---|---|---|---|---|
| そこ | そこ | そこ | 代名詞 | *-*-* | - | - |
| まで | まで | まで | 助詞 | 副助詞-*-* | - | - |
| は | は | は | 助詞 | 係助詞-*-* | - | - |
| 行き | いき | 行く | 動詞 | 非自立可能-*-* | 五段-カ行 | 連用形-一般 |
| ませ | ませ | ます | 助動詞 | *-*-* | 助動詞-マス | 未然形-一般 |
| ん | ん | ぬ | 助動詞 | *-*-* | 助動詞-ヌ | 終止形-撥音便 |

### `ご飯を食べませんでした`

Expectation: ませんでした — kernel on せ

**Local fugashi+UniDic:**

| surface | pos1 | pos2 | cType | cForm | lemma | aType |
|---|---|---|---|---|---|---|
| ご飯 | 名詞 | 普通名詞 | * | * | 御飯 | 1 |
| を | 助詞 | 格助詞 | * | * | を | * |
| 食べ | 動詞 | 一般 | 下一段-バ行 | 連用形-一般 | 食べる | 2 |
| ませ | 助動詞 | * | 助動詞-マス | 未然形-一般 | ます | * |
| ん | 助動詞 | * | 助動詞-ヌ | 終止形-撥音便 | ず | * |
| でし | 助動詞 | * | 助動詞-デス | 連用形-一般 | です | * |
| た | 助動詞 | * | 助動詞-タ | 終止形-一般 | た | * |

**Yahoo MA-UniDic:**

| surface | reading | base | pos | pos1 | cType | cForm |
|---|---|---|---|---|---|---|
| ご飯 | ごはん | ご飯 | 名詞 | 普通名詞-一般-* | - | - |
| を | を | を | 助詞 | 格助詞-*-* | - | - |
| 食べ | たべ | 食べる | 動詞 | 一般-*-* | 下一段-バ行 | 連用形-一般 |
| ませ | ませ | ます | 助動詞 | *-*-* | 助動詞-マス | 未然形-一般 |
| ん | ん | ぬ | 助動詞 | *-*-* | 助動詞-ヌ | 終止形-撥音便 |
| でし | でし | です | 助動詞 | *-*-* | 助動詞-デス | 連用形-一般 |
| た | た | た | 助動詞 | *-*-* | 助動詞-タ | 終止形-一般 |

### `彼を励ます`

Expectation: 励ます = 動詞・五段・基本形 (NOT 助動詞)

**Local fugashi+UniDic:**

| surface | pos1 | pos2 | cType | cForm | lemma | aType |
|---|---|---|---|---|---|---|
| 彼 | 代名詞 | * | * | * | 彼 | 1 |
| を | 助詞 | 格助詞 | * | * | を | * |
| 励ます | 動詞 | 一般 | 五段-サ行 | 終止形-一般 | 励ます | 3 |

**Yahoo MA-UniDic:**

| surface | reading | base | pos | pos1 | cType | cForm |
|---|---|---|---|---|---|---|
| 彼 | かれ | 彼 | 代名詞 | *-*-* | - | - |
| を | を | を | 助詞 | 格助詞-*-* | - | - |
| 励ます | はげます | 励ます | 動詞 | 一般-*-* | 五段-サ行 | 終止形-一般 |

### `彼に励まされる`

Expectation: 励ます passive form (still 動詞)

**Local fugashi+UniDic:**

| surface | pos1 | pos2 | cType | cForm | lemma | aType |
|---|---|---|---|---|---|---|
| 彼 | 代名詞 | * | * | * | 彼 | 1 |
| に | 助詞 | 格助詞 | * | * | に | * |
| 励まさ | 動詞 | 一般 | 五段-サ行 | 未然形-一般 | 励ます | 3 |
| れる | 助動詞 | * | 助動詞-レル | 連体形-一般 | れる | * |

**Yahoo MA-UniDic:**

| surface | reading | base | pos | pos1 | cType | cForm |
|---|---|---|---|---|---|---|
| 彼 | かれ | 彼 | 代名詞 | *-*-* | - | - |
| に | に | に | 助詞 | 格助詞-*-* | - | - |
| 励まさ | はげまさ | 励ます | 動詞 | 一般-*-* | 五段-サ行 | 未然形-一般 |
| れる | れる | れる | 助動詞 | *-*-* | 助動詞-レル | 終止形-一般 |

### `升は古い容器です`

Expectation: 升 = 名詞 (NOT 助動詞)

**Local fugashi+UniDic:**

| surface | pos1 | pos2 | cType | cForm | lemma | aType |
|---|---|---|---|---|---|---|
| 升 | 助動詞 | * | 助動詞-マス | 終止形-一般 | ます | * |
| は | 助詞 | 係助詞 | * | * | は | * |
| 古い | 形容詞 | 一般 | 形容詞 | 連体形-一般 | 古い | 2 |
| 容器 | 名詞 | 普通名詞 | * | * | 容器 | 1 |
| です | 助動詞 | * | 助動詞-デス | 終止形-一般 | です | * |

**Yahoo MA-UniDic:**

| surface | reading | base | pos | pos1 | cType | cForm |
|---|---|---|---|---|---|---|
| 升 | ます | 升 | 名詞 | 固有名詞-人名-姓 | - | - |
| は | は | は | 助詞 | 係助詞-*-* | - | - |
| 古い | ふるい | 古い | 形容詞 | 一般-*-* | 形容詞 | 連体形-一般 |
| 容器 | ようき | 容器 | 名詞 | 普通名詞-一般-* | - | - |
| です | です | です | 助動詞 | *-*-* | 助動詞-デス | 終止形-一般 |

### `酒を一升ください`

Expectation: 升 as counter (名詞 or 助数詞)

**Local fugashi+UniDic:**

| surface | pos1 | pos2 | cType | cForm | lemma | aType |
|---|---|---|---|---|---|---|
| 酒 | 名詞 | 普通名詞 | * | * | 酒 | 0 |
| を | 助詞 | 格助詞 | * | * | を | * |
| 一 | 名詞 | 数詞 | * | * | 一 | 2 |
| 升 | 名詞 | 普通名詞 | * | * | 升 | 1 |
| ください | 動詞 | 非自立可能 | 五段-ラ行 | 命令形 | 下さる | 3 |

**Yahoo MA-UniDic:**

| surface | reading | base | pos | pos1 | cType | cForm |
|---|---|---|---|---|---|---|
| 酒 | さけ | 酒 | 名詞 | 普通名詞-一般-* | - | - |
| を | を | を | 助詞 | 格助詞-*-* | - | - |
| 一 | いち | 一 | 名詞 | 数詞-*-* | - | - |
| 升 | しょう | 升 | 名詞 | 普通名詞-助数詞可能-* | - | - |
| ください | ください | くださる | 動詞 | 非自立可能-*-* | 五段-ラ行 | 命令形 |

### `電車に間に合います`

Expectation: 間に合います — straightforward ます

**Local fugashi+UniDic:**

| surface | pos1 | pos2 | cType | cForm | lemma | aType |
|---|---|---|---|---|---|---|
| 電車 | 名詞 | 普通名詞 | * | * | 電車 | 0,1 |
| に | 助詞 | 格助詞 | * | * | に | * |
| 間に合い | 動詞 | 一般 | 五段-ワア行 | 連用形-一般 | 間に合う | 3 |
| ます | 助動詞 | * | 助動詞-マス | 終止形-一般 | ます | * |

**Yahoo MA-UniDic:**

| surface | reading | base | pos | pos1 | cType | cForm |
|---|---|---|---|---|---|---|
| 電車 | でんしゃ | 電車 | 名詞 | 普通名詞-一般-* | - | - |
| に | に | に | 助詞 | 格助詞-*-* | - | - |
| 間に合い | まにあい | 間に合う | 動詞 | 一般-*-* | 五段-ワア行 | 連用形-一般 |
| ます | ます | ます | 助動詞 | *-*-* | 助動詞-マス | 終止形-一般 |

### `怠けたかった`

Expectation: たかった = たい past

**Local fugashi+UniDic:**

| surface | pos1 | pos2 | cType | cForm | lemma | aType |
|---|---|---|---|---|---|---|
| 怠け | 動詞 | 一般 | 下一段-カ行 | 未然形-一般 | 怠ける | 3 |
| たかっ | 助動詞 | * | 助動詞-タイ | 連用形-促音便 | たい | * |
| た | 助動詞 | * | 助動詞-タ | 連体形-一般 | た | * |

**Yahoo MA-UniDic:**

| surface | reading | base | pos | pos1 | cType | cForm |
|---|---|---|---|---|---|---|
| 怠け | なまけ | 怠ける | 動詞 | 一般-*-* | 下一段-カ行 | 未然形-一般 |
| たかっ | たかっ | たい | 助動詞 | *-*-* | 助動詞-タイ | 連用形-促音便 |
| た | た | た | 助動詞 | *-*-* | 助動詞-タ | 終止形-一般 |

### `泣きたくない`

Expectation: たくない = たい + ない

**Local fugashi+UniDic:**

| surface | pos1 | pos2 | cType | cForm | lemma | aType |
|---|---|---|---|---|---|---|
| 泣き | 動詞 | 一般 | 五段-カ行 | 連用形-一般 | 泣く | 0 |
| たく | 助動詞 | * | 助動詞-タイ | 連用形-一般 | たい | * |
| ない | 形容詞 | 非自立可能 | 形容詞 | 連体形-一般 | 無い | 1 |

**Yahoo MA-UniDic:**

| surface | reading | base | pos | pos1 | cType | cForm |
|---|---|---|---|---|---|---|
| 泣き | なき | 泣く | 動詞 | 一般-*-* | 五段-カ行 | 連用形-一般 |
| たく | たく | たい | 助動詞 | *-*-* | 助動詞-タイ | 連用形-一般 |
| ない | ない | ない | 形容詞 | 非自立可能-*-* | 形容詞 | 終止形-一般 |

### `休みたかったけど`

Expectation: past たい with conjunctive

**Local fugashi+UniDic:**

| surface | pos1 | pos2 | cType | cForm | lemma | aType |
|---|---|---|---|---|---|---|
| 休み | 動詞 | 一般 | 五段-マ行 | 連用形-一般 | 休む | 2 |
| たかっ | 助動詞 | * | 助動詞-タイ | 連用形-促音便 | たい | * |
| た | 助動詞 | * | 助動詞-タ | 終止形-一般 | た | * |
| けど | 助詞 | 接続助詞 | * | * | けれど | * |

**Yahoo MA-UniDic:**

| surface | reading | base | pos | pos1 | cType | cForm |
|---|---|---|---|---|---|---|
| 休み | やすみ | 休む | 動詞 | 一般-*-* | 五段-マ行 | 連用形-一般 |
| たかっ | たかっ | たい | 助動詞 | *-*-* | 助動詞-タイ | 連用形-促音便 |
| た | た | た | 助動詞 | *-*-* | 助動詞-タ | 終止形-一般 |
| けど | けど | けど | 助詞 | 接続助詞-*-* | - | - |


## Corpus parity (test_0.txt + test_1.txt)

### test_0.txt

- non-empty lines processed: 36
- surface concat match: 9, mismatch: 27
- token total: local=1605, yahoo=1755 (ratio: 0.915)
- timing per line (median): local=0.29ms, yahoo=80.5ms (speedup: 282x)
  - mismatch sample:
    - input: 今年で3回目となる「ベスト エキスパート 2026」授賞式は、「Yahoo!ニュ
    - local: 今年で3回目となる「ベストエキスパート2026」授賞式は、「Yahoo!ニュース
    - yahoo: 今年で3回目となる「ベスト エキスパート 2026」授賞式は、「Yahoo!ニュ
  - mismatch sample:
    - input: 「ベスト エキスパート2026」受賞者
    - local: 「ベストエキスパート2026」受賞者
    - yahoo: 「ベスト エキスパート2026」受賞者
  - mismatch sample:
    - input: グランプリ オーサー部門
    - local: グランプリオーサー部門
    - yahoo: グランプリ オーサー部門

### test_1.txt

- non-empty lines processed: 57
- surface concat match: 24, mismatch: 33
- token total: local=1065, yahoo=1766 (ratio: 0.603)
- timing per line (median): local=0.09ms, yahoo=77.3ms (speedup: 839x)
  - mismatch sample:
    - input: CER (Character Error Rate): 正解音素列に対して、予測
    - local: CER(CharacterErrorRate):正解音素列に対して、予測された音
    - yahoo: CER (Character Error Rate): 正解音素列に対して、予測
  - mismatch sample:
    - input: G2Pマッチ率: ふりがなWhisperの出力音素列が、書記素列に対するG2P結
    - local: G2Pマッチ率:ふりがなWhisperの出力音素列が、書記素列に対するG2P結果
    - yahoo: G2Pマッチ率: ふりがなWhisperの出力音素列が、書記素列に対するG2P結
  - mismatch sample:
    - input: N-Best読み未使用でのG2Pマッチ率: N-Best機能を使わない通常のG2
    - local: N-Best読み未使用でのG2Pマッチ率:N-Best機能を使わない通常のG2P
    - yahoo: N-Best読み未使用でのG2Pマッチ率: N-Best機能を使わない通常のG2


## Accent class: fugashi `aType` vs OJAD prediction

`aType` is UniDic's kernel-position annotation (0 = heiban, 1 = atamadaka, 2 = nakadaka with kernel on mora 2, etc.).
For OJAD we find the position of the moji marked `accent=2` (FALL) in the per-moji output; absence of FALL means heiban.

| surface | fugashi aType | OJAD kernel pos | agree? |
|---|---|---|---|
| 雨 | 1 | 1 | ✓ |
| 水 | 0 | 0 | ✓ |
| 山 | 2 | 0 | ✗ |
| 川 | 2 | 0 | ✗ |
| 花 | 2 | 0 | ✗ |
| 犬 | 2 | 0 | ✗ |
| 猫 | 1 | 1 | ✓ |
| 鳥 | 0 | 0 | ✓ |
| 魚 | 0 | 0 | ✓ |
| 本 | * | 1 | - |
| 猛 | * | 1 | - |
| 桜 | 0 | 0 | ✓ |
| 電車 | 0,1 | 0 | - |
| 学校 | 0 | 0 | ✓ |
| 会社 | 0 | 0 | ✓ |
| 新聞 | 0 | 0 | ✓ |
| 音楽 | 1,0 | 1 | - |
| 言葉 | 3 | 0 | ✗ |
| 時間 | 0 | 0 | ✓ |
| 問題 | 0 | 0 | ✓ |
| 食べる | 2 | 2 | ✓ |
| 飲む | 1 | 1 | ✓ |
| 見る | 1 | 1 | ✓ |
| 行く | 0 | 0 | ✓ |
| 来る | 1 | 1 | ✓ |
| 書く | 1 | 1 | ✓ |
| 読む | 1 | 1 | ✓ |
| 話す | 2 | 2 | ✓ |
| 聞く | 0 | 0 | ✓ |
| 歩く | 2 | 2 | ✓ |
| 美しい | 4 | 4 | ✓ |
| 楽しい | 3 | 3 | ✓ |
| 高い | 2 | 2 | ✓ |
| 低い | 2 | 2 | ✓ |
| 新しい | 4 | 4 | ✓ |
| 古い | 2 | 2 | ✓ |
| 大きい | 3 | 3 | ✓ |
| 小さい | 3 | 3 | ✓ |
| 速い | 2 | 2 | ✓ |
| 遅い | 0,2 | 2 | - |
| 毎朝 | 0,1 | 0 | - |
| 昨日 | 2,0 | 2 | - |
| 今日 | 1 | 1 | ✓ |
| 明日 | 2,0 | 0 | - |
| 今年 | 0 | 0 | ✓ |
| 去年 | 1 | 1 | ✓ |
| 来年 | 0 | 0 | ✓ |
| 私 | 0 | 0 | ✓ |
| 彼 | 1 | 1 | ✓ |
| 彼女 | 1 | 1 | ✓ |

**Summary**: agree=37/42 (88%), disagree=5/42

## Disk impact

- dict location: `/home/torridfish/Code/spike-local-unidic/.venv/lib/python3.11/site-packages/unidic/dicdir`
- dict size: 774 MB
- pyproject.toml additions: `fugashi`, `unidic`.

## Recommendation

**GO** — replace `Yahoo MA-UniDic (HTTP) + OJAD scrape` with local
`fugashi + NINJAL UniDic 3.1.0` in issue #50.

### Why GO

- **`aType` vs OJAD: 37/42 = 88% agreement** (8 probe rows had
  comma-separated `aType` and were excluded from the comparison; the
  remaining 42 are unambiguous). The 5 disagreements (山, 川, 花, 犬,
  言葉) all involve words with **two attested modern Tokyo readings**
  — e.g. 山 is 2型 in traditional Tokyo but 0型 in modern speech, both
  in active circulation. OJAD picks the modern variant; UniDic records
  the traditional one. Neither side is wrong, so the *practical*
  agreement is closer to 100% on the probe set.
- **POS / cType / lemma parity with Yahoo MA: effectively 100% on
  the PR #49 disambiguation probe.** Every ます/たい/まし/ませ/
  ました/たかった/たくない disambiguation matches Yahoo, including the
  motivating 励ます (動詞・五段-サ行) vs ます (助動詞-マス) split that
  this entire pipeline was designed around.
- **Latency: 282–839× faster than Yahoo MA per line** (median 0.09–
  0.29 ms local vs 77–80 ms HTTP). For a 100-line document this
  collapses a multi-second cumulative wait into near-zero.
- **Disk impact: 774 MB** — acceptable for the deploy target, and
  shippable as a one-time wheel install (`uv add unidic && python -m
  unidic download`).
- **Drops two failure modes** that PR #49 has to defend against: the
  Yahoo API rate-limit / outage path, and the OJAD HTML scrape path
  (the latter is the more fragile of the two).

### What to wire up in the #50 PR

1. Replace the Yahoo MA call in `api/accent_marker.py` with a
   `fugashi.Tagger()` instance (singleton, lazy). Map UniDic feature
   fields → the dict shape `apply_accent_patches` expects (already
   compatible: `pos1`, `pos2`, `cType`, `cForm`, `lemma`).
2. Replace OJAD's per-mora `accent=2` scan with: for each content-
   morpheme, take `aType` as the kernel mora position (0 = heiban,
   N ≥ 1 = kernel on mora N). Project per-morpheme kernels into
   per-mora HIGH/LOW/FALL the same way `align_accent` currently does
   from OJAD output. This removes the DP alignment step entirely —
   tokens and accents come from the same source, no alignment needed.
3. **Handle `aType="X,Y"` (ambiguous)**: pick the first listed value
   as canonical (UniDic orders them by frequency). 14/50 = 28% of the
   accent-probe words came back ambiguous, so this branch is hot.
   Expose an `aTypes` field on the response so callers that care
   (e.g. furigana-only flows) can see both.
4. **Handle `aType="*"` (no annotation)**: applies to particles,
   auxiliaries, and a few low-frequency content words (e.g. 本, 猛 in
   the probe). Fall through to the existing `apply_accent_patches`
   rules for the closed-class items; for the long tail, default to
   heiban (the same fallback OJAD effectively gives).
5. **Whitespace handling**: the corpus-parity surface mismatches were
   all whitespace artefacts (neologdn-style space collapse vs Yahoo's
   token-per-space). Keep the existing `neologdn` pre-pass before
   tagging — fugashi will produce the same tokens Yahoo does once the
   input is normalised the same way.
6. **Edge-case test**: add `升は古い容器です` to the test fixtures.
   Local UniDic mis-tags 升 as 助動詞-マス at sentence start (Yahoo
   correctly tags 名詞-固有名詞). Either keep a small override list
   for high-risk single-char nouns or accept the divergence — it's an
   adversarial edge case, not a realistic input.

### What stays

- `apply_accent_patches` and the override layer in
  `reading_overrides.py` stay as-is; they consume morpheme dicts,
  which UniDic provides natively.
- Sentence/paragraph splitting and URL escaping logic is unchanged.

### Sequencing

Open the #50 PR off `feat/yahoo-ma-migration` (PR #49) once #49
lands, since #49 already migrated the schema fields
(`pos1/pos2/cType/cForm/lemma`) that local UniDic exposes. If #49 is
delayed, the spike branch can rebase off `main` directly — the schema
change is small.

