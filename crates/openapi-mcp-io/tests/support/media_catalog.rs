//! A local media API and test executor using only the public generic interfaces.

use std::sync::Arc;
use std::time::Duration;

use axum::{
    Json, Router,
    extract::{Multipart, Request, State},
    middleware::{self, Next},
    response::Response as HttpResponse,
    routing::{get, patch, post},
};
use http::{HeaderMap, Method, StatusCode, Uri, header};
use openapi_mcp_core::{self as api, Policy, ToolDefinition, ToolKind, ToolSet};
use openapi_mcp_io::HttpExecutor;
use openapi_mcp_io::api::{FileReadPolicy, run};
use openapi_mcp_spec::OpenApiSpec;
use rmcp::model::CallToolResult;
use serde_json::{Value, json};
use tokio::{sync::Mutex, task::JoinHandle};

pub const BINARY_CONTENT: &[u8] = &[0, 255, 10, 13, 128];
pub const TOOL_NAMES: [&str; 4] = ["media.find", "media.describe", "media.send", "media.type"];

const POLICY: Policy = Policy {
    present_endpoint: |_, _| {},
    present_schema: |_, _| {},
    validate_body: |_, _, _| Ok(()),
    validate_request: |_| Ok(()),
    append_error_details: |_, _| {},
};

/// Record the request as received by the server, before routing or body parsing.
#[derive(Clone, Debug)]
pub struct ObservedRequest {
    pub method: Method,
    pub uri: Uri,
    pub headers: HeaderMap,
}

type Observations = Arc<Mutex<Vec<ObservedRequest>>>;

async fn observe(
    State(observations): State<Observations>,
    request: Request,
    next: Next,
) -> HttpResponse {
    observations.lock().await.push(ObservedRequest {
        method: request.method().clone(),
        uri: request.uri().clone(),
        headers: request.headers().clone(),
    });
    next.run(request).await
}

/// Echo parsed multipart parts so tests can check the bytes and field names.
async fn upload(mut multipart: Multipart) -> (StatusCode, Json<Value>) {
    let mut parts = Vec::new();
    while let Some(field) = multipart.next_field().await.expect("valid multipart field") {
        let name = field.name().expect("named form field").to_owned();
        let data = field.bytes().await.expect("complete form field");
        parts.push(json!({"name": name, "data": data.to_vec()}));
    }
    (StatusCode::CREATED, Json(json!(parts)))
}

fn tools() -> ToolSet {
    ToolSet::new(&[
        ToolDefinition {
            kind: ToolKind::Search,
            name: TOOL_NAMES[0],
            description: "Find media operations, then inspect them with media.describe.",
            input_description: None,
            field_descriptions: &[],
        },
        ToolDefinition {
            kind: ToolKind::Explain,
            name: TOOL_NAMES[1],
            description: "Explain a media operation found with media.find.",
            input_description: None,
            field_descriptions: &[],
        },
        ToolDefinition {
            kind: ToolKind::Call,
            name: TOOL_NAMES[2],
            description: "Send a media request described by media.describe.",
            input_description: None,
            field_descriptions: &[("body", "Use media.describe to inspect the request schema.")],
        },
        ToolDefinition {
            kind: ToolKind::Schema,
            name: TOOL_NAMES[3],
            description: "Inspect a media component schema.",
            input_description: None,
            field_descriptions: &[],
        },
    ])
    .expect("valid media tool configuration")
}

/// Own the API server for one test and stop it even when an assertion fails.
pub struct Catalog {
    pub tools: ToolSet,
    spec: OpenApiSpec,
    executor: HttpExecutor,
    observations: Observations,
    server: JoinHandle<()>,
}

impl Catalog {
    pub async fn start() -> Self {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind media API");
        let address = listener.local_addr().expect("media API address");
        let observations = Observations::default();
        let app = Router::new()
            .route(
                "/api/v2/collections/{collection_id}/items/{item_id}",
                patch(|Json(body): Json<Value>| async move { Json(body) }),
            )
            .route("/api/v2/collections/{collection_id}/assets", post(upload))
            .route(
                "/api/v2/assets/{asset_id}/content",
                get(|| async {
                    (
                        [(header::CONTENT_TYPE, "application/octet-stream")],
                        BINARY_CONTENT,
                    )
                }),
            )
            .route(
                "/api/v2/busy",
                get(|| async {
                    (
                        StatusCode::SERVICE_UNAVAILABLE,
                        [(header::RETRY_AFTER, "7")],
                        Json(json!({"code": "capacity"})),
                    )
                }),
            )
            .layer(middleware::from_fn_with_state(
                Arc::clone(&observations),
                observe,
            ));

        let mut document: Value =
            serde_json::from_str(include_str!("../fixtures/media-catalog.json"))
                .expect("media catalog fixture");
        document["servers"][0]["url"] = json!(format!("http://{address}/api/v2/"));
        let spec = OpenApiSpec::from_json(&document.to_string()).expect("parse media API");
        let executor = HttpExecutor::with_client(
            reqwest::Client::builder()
                .no_proxy()
                .timeout(Duration::from_secs(5))
                .build()
                .expect("local HTTP client"),
            spec.server_url(),
            HeaderMap::new(),
        )
        .expect("valid executor");
        let server = tokio::spawn(async move {
            axum::serve(listener, app).await.expect("serve media API");
        });
        Self {
            tools: tools(),
            spec,
            executor,
            observations,
            server,
        }
    }

    /// Resolve the configured name and run the public engine through to its MCP result.
    pub async fn invoke(
        &mut self,
        name: &str,
        arguments: Value,
        file_reads: FileReadPolicy<'_>,
    ) -> CallToolResult {
        let kind = self.tools.resolve(name).expect("configured media tool");
        let effect = api::dispatch(kind, arguments.as_object(), &self.spec, &POLICY);
        run(effect, &POLICY, &mut self.executor, file_reads)
            .await
            .expect("MCP result")
    }

    pub async fn requests(&self) -> Vec<ObservedRequest> {
        self.observations.lock().await.clone()
    }
}

impl Drop for Catalog {
    fn drop(&mut self) {
        self.server.abort();
    }
}
