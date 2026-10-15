from waitress import serve
from .app import create_app

if __name__ == "__main__":
    serve(create_app(), host="127.0.0.1", port=8030, threads=4, trusted_proxy="127.0.0.1", clear_untrusted_proxy_headers=False)
