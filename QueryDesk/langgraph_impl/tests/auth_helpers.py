import os

PASSWORD="Only-for-local-tests-7843!"
SETUP_TOKEN="unit-test-setup-secret-that-is-long-enough"
os.environ["AUTH_SETUP_TOKEN"]=SETUP_TOKEN


def approved_model_policy(endpoint='https://api.openai.com/v1',model='test'):
    from langgraph_impl.model_policy import ModelPolicy
    return ModelPolicy({'version':1,'sources':{'sample':{'classification':'internal'}},'profiles':[{'id':'fixture','endpoint':endpoint,'models':[model],'approved':True,'classifications':['internal']}]})


def sign_in_admin(client):
    endpoint="setup" if client.get('/api/auth/status').json()['setup_required'] else "login"
    payload={"username":"testadmin","password":PASSWORD}
    if endpoint=="setup":
        payload["setup_token"]=SETUP_TOKEN
    response=client.post('/api/auth/'+endpoint,json=payload)
    assert response.status_code==200,response.text
    client.headers['X-CSRF-Token']=response.json()['csrf_token']
    return response.json()['user']
