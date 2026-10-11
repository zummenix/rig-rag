use axum::{
    Json,
    extract::State,
    response::{IntoResponse, Response},
};
use shared::QueryRequest;

use crate::retrieval::THRESHOLD;
use crate::serve::{internal_error, state::AppState};

/// `POST /api/query` — validate `k`, retrieve, return hits (possibly empty).
pub async fn handler(State(state): State<AppState>, Json(request): Json<QueryRequest>) -> Response {
    let k = request.effective_k();

    match state
        .retriever
        .retrieve(&request.question, k, THRESHOLD)
        .await
    {
        Ok(hits) => Json(hits).into_response(),
        Err(error) => internal_error(error),
    }
}
