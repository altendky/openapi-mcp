#![allow(clippy::expect_used, clippy::panic)]

use http::{HeaderMap, Method, StatusCode};
use openapi_mcp_io::{
    HttpExecutor,
    api::{RequestExecutor, RequestOutcome},
};
use openapi_mcp_spec::request::ApiRequest;

fn request(path: &str) -> ApiRequest {
    ApiRequest {
        method: Method::GET,
        path: path.into(),
        query_params: vec![],
        headers: HeaderMap::new(),
        body: None,
        content_type: None,
    }
}

#[test]
fn base_urls_reject_credentials_and_non_server_components() {
    for url in [
        "relative/path",
        "file:///tmp/api",
        "https://user:secret@example.com",
        "https://example.com?token=secret",
        "https://example.com#fragment",
    ] {
        assert!(HttpExecutor::new(url, HeaderMap::new()).is_err(), "{url}");
    }
    assert!(HttpExecutor::new("https://example.com/api/v2/", HeaderMap::new()).is_ok());
}

#[tokio::test]
async fn redirects_are_returned_without_following_them() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("listen");
    let address = listener.local_addr().expect("address");
    let router = axum::Router::new().route(
        "/redirect",
        axum::routing::get(|| async {
            (
                StatusCode::TEMPORARY_REDIRECT,
                [(http::header::LOCATION, "/redirect")],
            )
        }),
    );
    let server = tokio::spawn(async move { axum::serve(listener, router).await.expect("serve") });
    let mut executor =
        HttpExecutor::new(&format!("http://{address}"), HeaderMap::new()).expect("executor");
    let result = executor
        .execute(request("/redirect"))
        .await
        .expect("response");
    let RequestOutcome::Response(response) = result else {
        panic!("expected HTTP response")
    };
    assert_eq!(response.status, 307);
    server.abort();
}

#[tokio::test]
async fn transport_errors_do_not_include_query_values() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("reserve port");
    let address = listener.local_addr().expect("address");
    drop(listener);
    let client = reqwest::Client::builder()
        .no_proxy()
        .build()
        .expect("client");
    let mut executor =
        HttpExecutor::with_client(client, &format!("http://{address}"), HeaderMap::new())
            .expect("executor");
    let mut request = request("/private-path");
    request.query_params = vec![("token".into(), "do-not-display".into())];
    let error = executor
        .execute(request)
        .await
        .err()
        .expect("connection failure");
    assert!(!error.message.contains("do-not-display"));
    assert!(!error.message.contains("private-path"));
}

#[tokio::test]
async fn relative_request_paths_are_rejected_before_http() {
    let mut executor =
        HttpExecutor::new("https://example.invalid", HeaderMap::new()).expect("executor");
    let error = executor
        .execute(request("relative"))
        .await
        .err()
        .expect("invalid path");
    assert!(error.message.contains("must start with '/'"));
}
