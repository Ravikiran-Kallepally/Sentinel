# 02 - AI and ML foundations

The vocabulary and mental models you need for every later module. Each concept points to where it lives in
the code, so it never stays abstract.

## Machine learning in one paragraph

A **model** is a function learned from examples. You give it **features** (numbers describing an example: the
amount, the hour, the distance from home) and, for **supervised** learning, a **label** (was it fraud?). Training
adjusts the model so its predictions match the labels. The only question that matters is how well it does on
examples it has **never seen** - so you always hold out a **test set**.

## Supervised vs unsupervised

- **Supervised**: learn from labelled examples. Classification (fraud yes/no, complaint category) and regression
  / forecasting (how many disputes tomorrow). Code: `ml/fraud.py`, `ml/nlp.py`, `ml/forecast.py`.
- **Unsupervised**: no labels; learn the shape of "normal" and flag what does not fit. Code: `ml/anomaly.py`.

## The model families you will meet here

| Family | Intuition | Here |
|---|---|---|
| Logistic regression | Weighted sum of features squashed into a probability. Simple, fast, explainable. | the fraud baseline, the complaint classifier |
| Gradient-boosted trees | Hundreds of small decision trees, each fixing the previous ones' errors. Usually the best choice for tabular data. | `HistGradientBoostingClassifier` in `ml/fraud.py` |
| Isolation forest | Random splits isolate outliers quickly. | `ml/anomaly.py` |
| Neural network / autoencoder | Layers of learned transformations; an autoencoder learns to compress and rebuild normal data. | `ml/anomaly.py` (PyTorch) |
| LSTM | A neural network with memory, for sequences such as daily volumes. | `ml/forecast.py` |
| Transformer / LLM | A huge neural network trained to predict the next token of text; follows instructions after further training. | everything in `llm/` |

## Overfitting and leakage - the two classic failures

- **Overfitting**: the model memorises training data and fails on new data. Symptoms: training metrics far above
  test metrics. Defences: hold-out data, regularisation, early stopping, simpler models.
- **Leakage**: information from the future or from the label sneaks into the features, so test metrics look
  amazing and production performance collapses. This repo defends against it three ways: time-based splits,
  point-in-time features (`ml/features.py`), and target encoding fitted on training data only (`ml/fraud.py`).
  If a metric looks too good, suspect leakage or unrealistically clean data first. We hit exactly this while
  building the simulator: the first version gave PR-AUC 0.995 because fraud was detectable from "distance from
  home" and "new device" alone. Real fraud overlaps with legitimate behaviour, so the simulator now adds travel,
  phone upgrades, proxies near the victim, "low and slow" fraud and noisy labels (see the comments in
  `data/synth.py`) - and PR-AUC dropped to a realistic 0.72.

## Metrics: pick the one that matches the decision

| Metric | Use when | Here |
|---|---|---|
| Accuracy | classes are balanced | rarely - useless for fraud (99.5% "accurate" by predicting "never fraud") |
| Precision | false alarms are expensive | analyst workload |
| Recall | misses are expensive | missed fraud |
| PR-AUC | positives are rare; you care about the alert list | fraud headline metric |
| ROC-AUC | balanced-ish classes | shown next to PR-AUC to teach why it misleads |
| Macro-F1 | several classes, all matter equally | complaint categories |
| MAE / RMSE | numeric forecasts | dispute volumes |

A **threshold** turns a probability into a decision. Choosing it is a business decision, not a modelling one:
`ml/fraud.py::cost_optimal_threshold` picks it by minimising missed-fraud dollars plus review cost.

## Embeddings

An **embedding** maps text to a vector of numbers so that similar meanings are close together. "Card was stolen"
and "unauthorized use" end up near each other even with no shared words. This powers semantic search in RAG
(`rag/embeddings.py`). Cosine similarity measures closeness.

## Large language models - what you must know as an engineer

- **Tokens**: models read and write tokens (~3/4 of a word). Cost and limits are in tokens.
- **Context window**: everything the model can see in one request: instructions + documents + conversation.
  More context costs more and can distract the model, which is why RAG retrieves only the best few chunks.
- **System prompt**: the operator's instructions. **User/tool content**: data. Keep them separate (module 08).
- **Non-determinism**: the same input can produce different outputs. Hence schema validation, evals and tests
  with a deterministic stand-in (`llm/offline.py`).
- **Tool use (function calling)**: the model can request a function call with JSON arguments; your code runs it
  and returns the result. This is how agents act (module 07).
- **Effort / reasoning**: current models think before answering; you trade quality for cost and latency with
  settings such as Claude's `effort` level (`llm/providers.py`).
- **Hallucination**: fluent, confident, wrong. The cure is grounding (RAG with citations), validation, and saying
  "I don't know" (module 06).

## RAG and agents in one sentence each

- **RAG**: fetch the relevant documents first, then ask the model to answer only from them, with citations.
- **Agent**: a model in a loop that decides which tools to call next until a goal is met - bounded by budgets
  and, in regulated settings, supervised by a human.

## Exercises

1. In `ml/fraud.py`, temporarily replace the time-based split with a random split
   (`feats.sample(frac=1, random_state=0)` before slicing). Retrain (`python -m sentinel.cli train`). Did PR-AUC
   go up? Why is that number a lie? Revert.
2. Compute by hand: 1,000 transactions, 5 fraud. A model flags 20; 4 of them are fraud. Precision? Recall?
   What is the accuracy of a model that flags nothing?
3. Open the Model lab page and explain to an imaginary manager why the logistic baseline has the higher ROC-AUC
   but the boosted model is the better fraud model.

## Check yourself

- What is the difference between overfitting and leakage, and which is more dangerous? Why?
- Why is accuracy a bad fraud metric? What would you report instead?
- In your own words: what is an embedding and why does RAG need one?
- What is a token, and why do engineers care about them?
