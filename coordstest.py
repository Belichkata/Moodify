from flask import Flask, request

app = Flask(__name__)

@app.route("/gps", methods=["GET", "POST"])
def receive_gps():
    print("=== RAW QUERY STRING ===")
    print(request.query_string)

    print("=== GET PARAMS ===")
    print(request.args)

    print("=== POST PARAMS ===")
    print(request.form)

    return "OK"

app.run(host="0.0.0.0", port=8000)

