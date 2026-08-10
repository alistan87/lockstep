"""reference_split.py — a correct settlement module.

The contract  asks its models for, written out
once so other things can lean on it:  stages it next
to a generated server (which does , and
crashes on import without it), and it doubles as the worked answer for anyone
reading the flow who wants to know what "correct" meant.

NOT used by  — that gate carries its own reference on purpose,
so a change here cannot quietly redefine what the gate accepts.
"""
def balances(expenses):
    net = {}
    for e in expenses:
        share = e["amount"] / len(e["participants"])
        net[e["payer"]] = net.get(e["payer"], 0.0) + e["amount"]
        for p in e["participants"]:
            net[p] = net.get(p, 0.0) - share
    return net


def settle(bal):
    cred = sorted([[p, v] for p, v in bal.items() if v > 0.005], key=lambda x: -x[1])
    debt = sorted([[p, -v] for p, v in bal.items() if v < -0.005], key=lambda x: -x[1])
    out, i, j = [], 0, 0
    while i < len(cred) and j < len(debt):
        amt = min(cred[i][1], debt[j][1])
        out.append({"from": debt[j][0], "to": cred[i][0], "amount": round(amt, 2)})
        cred[i][1] -= amt
        debt[j][1] -= amt
        if cred[i][1] <= 0.005:
            i += 1
        if debt[j][1] <= 0.005:
            j += 1
    return out
