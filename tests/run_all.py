"""
Runs the station's checks on the Pi, from the station folder:

    python3 tests/run_all.py                       # the test suites
    python3 tests/run_all.py --against DIR          # and compare the screens and a simulated day with a baseline in DIR

A baseline is made once, before a change, with --baseline DIR: it freezes the station's state there, records what
every screen draws, and runs a simulated Friday. The browser tests (schedule_browser_test, health_browser_test) run
on the PC instead.
"""
import os
import subprocess
import sys
import time

TESTS = os.path.dirname(os.path.abspath(__file__))
SUITES = ["health_test", "weather_test", "scores_test", "art_test", "player_test", "live_test", "playlog_test", "onthisday_test", "launch_timing",
          "music_join_test", "remote_test", "remote_test3", "repeats_test"]
SIM_DAY = ["2026,11,6,9,0", "24", "7"]   # a Friday from sign-on: shows, the marathon and late night


def run(args, env=None, timeout=900):
    t0 = time.time()
    done = subprocess.run([sys.executable, *args], capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, **(env or {})})
    return done.returncode, (done.stdout + done.stderr).strip().splitlines(), time.time() - t0


def sim(frozen, out):
    os.makedirs("/tmp/sim", exist_ok=True)
    code, lines, _ = run(["-I", os.path.join(TESTS, "sim_grid.py"), *SIM_DAY], env={"FROZEN": frozen})
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
    return code


def main():
    args = sys.argv[1:]
    if args[:1] == ["--baseline"]:
        folder = os.path.abspath(args[1])
        sys.path.insert(0, TESTS)
        import harness
        harness.freeze(os.path.join(folder, "frozen"))
        weather = os.path.join(harness.HOME, "retrotv_weather.json")
        if os.path.exists(weather):
            subprocess.run(["cp", weather, os.path.join(folder, "frozen")])
        frozen = os.path.join(folder, "frozen")
        code, lines, _ = run([os.path.join(TESTS, "snapshots.py"), "record", os.path.join(folder, "snapshots")], env={"FROZEN": frozen})
        print(f"snapshots recorded: {'ok' if code == 0 else lines[-1:]}")
        print(f"simulated day recorded: {'ok' if sim(frozen, os.path.join(folder, 'sim.txt')) == 0 else 'failed'}")
        return
    failed = 0
    for name in SUITES:
        try:
            code, lines, took = run(["-I", os.path.join(TESTS, name + ".py")])
        except subprocess.TimeoutExpired:
            code, lines, took = 1, ["timed out"], 0
        failed += code != 0
        said = next((line for line in reversed(lines) if "passed" in line), lines[-1] if lines else "")
        print(f"{'ok  ' if code == 0 else 'FAIL'} {name:18s} {took:5.0f} s  {said[:100]}")
    if args[:1] == ["--against"]:
        folder = os.path.abspath(args[1])
        frozen = os.path.join(folder, "frozen")
        code, lines, _ = run([os.path.join(TESTS, "snapshots.py"), "compare", os.path.join(folder, "snapshots")], env={"FROZEN": frozen})
        failed += code != 0
        print(f"{'ok  ' if code == 0 else 'FAIL'} {'screens':18s}         " + next((l for l in reversed(lines) if "draw" in l), "")[:200])
        out = "/tmp/sim/compare.txt"
        code = sim(frozen, out)
        with open(out) as f, open(os.path.join(folder, "sim.txt")) as g:
            same = code == 0 and f.read() == g.read()
        failed += not same
        print(f"{'ok  ' if same else 'FAIL'} {'simulated day':18s}        {'the same as the baseline' if same else 'differs: diff ' + out + ' ' + os.path.join(folder, 'sim.txt')}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
