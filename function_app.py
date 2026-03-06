import azure.functions as func
import datetime
import json
import logging

app = func.FunctionApp()


@app.function_name(name="getanalyzedresponse")
@app.route(route="process", methods=["POST"])
def get_analyzed_response(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Processing a new request.')
    
    try:
        request_body = req.get_json()
        logging.info(f'Request received: {request_body}')
        
        return func.HttpResponse(
            json.dumps({'status': 'success', 'received': request_body}),
            status_code=200,
            mimetype='application/json'
        )
    except Exception as e:
        logging.error(f'Error processing request: {str(e)}')
        return func.HttpResponse(
            json.dumps({'error': str(e)}),
            status_code=400,
            mimetype='application/json'
        )