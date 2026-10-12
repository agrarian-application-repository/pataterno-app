#!/usr/bin/env python3
"""Application-sized transfer times across the satellite link.

Each sample opens a NEW TCP connection (as an HTTP POST would), sends N bytes,
half-closes, and waits for the receiver's 2-byte acknowledgement, so the time
includes the handshake, TCP slow start and the reply - what an application
upload actually costs over a ~500 ms RTT link.

  server (on ws_gw):  python3 transfer_test.py serve [port]
  client (on ws_st):  python3 transfer_test.py run <host> [port] [reps]
The client prints one JSON document on stdout.
"""
import json
import socket
import sys
import time

SIZES = [1_000, 10_000, 150_000, 1_000_000, 5_000_000]   # 1 KB alert ... 5 MB


def serve(port: int) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(8)
    while True:
        conn, _ = srv.accept()
        with conn:
            while conn.recv(65536):
                pass
            conn.sendall(b"ok")


def run(host: str, port: int, reps: int) -> None:
    out = {"host": host, "port": port, "reps": reps, "results": []}
    for size in SIZES:
        payload = b"\0" * size
        samples = []
        for _ in range(reps):
            t0 = time.monotonic()
            s = socket.create_connection((host, port), timeout=120)
            t_conn = time.monotonic()
            s.sendall(payload)
            s.shutdown(socket.SHUT_WR)
            ack = s.recv(2)
            t_end = time.monotonic()
            s.close()
            samples.append({"connect_s": round(t_conn - t0, 3), "total_s": round(t_end - t0, 3),
                            "ok": ack == b"ok"})
        tot = sorted(x["total_s"] for x in samples)
        out["results"].append({
            "bytes": size,
            "median_total_s": tot[len(tot) // 2],
            "min_total_s": tot[0],
            "max_total_s": tot[-1],
            "goodput_mbps_median": round(size * 8 / tot[len(tot) // 2] / 1e6, 3),
            "samples": samples,
        })
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "serve":
        serve(int(sys.argv[2]) if len(sys.argv) > 2 else 5599)
    elif len(sys.argv) >= 3 and sys.argv[1] == "run":
        run(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 5599,
            int(sys.argv[4]) if len(sys.argv) > 4 else 3)
    else:
        print(__doc__)
        sys.exit(2)
