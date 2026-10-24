#!/usr/bin/env python3
"""Dialogue Lab: resumable two-provider conversation, standard library only."""
import json, os, re, sys, time, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'data/state.json'; TRANSCRIPT=ROOT/'data/transcript.json'; OUTCOMES=ROOT/'data/outcomes.json'
NOW=lambda: datetime.now(timezone.utc).isoformat(timespec='seconds')
def load(path,default):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError: return default
def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp'); temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); temp.replace(path)
def request(url,headers,payload):
    req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={**headers,'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=65) as response: return json.load(response)
    except urllib.error.HTTPError as e:
        detail=e.read(400).decode('utf-8','replace')
        raise RuntimeError(f'HTTP {e.code}: {detail}') from e

def call_model(who,messages):
    if who=='A':
        key=os.environ['GEMINI_API_KEY']; model=os.getenv('GEMINI_MODEL','gemini-3.1-flash-lite')
        url=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
        content=[{'role': 'model' if x['speaker']==who else 'user','parts':[{'text':x['text']}]} for x in messages]
        prompt=SYSTEM_PROMPT
        result=request(url,{'x-goog-api-key':key},{'systemInstruction':{'parts':[{'text':prompt}]},'contents':content,'generationConfig':{'maxOutputTokens':700,'temperature':0.8}})
        choices=result.get('candidates') or []
        if not choices: raise RuntimeError('Gemini returned no candidates')
        parts=choices[0].get('content',{}).get('parts',[])
        text=' '.join(p.get('text','') for p in parts).strip()
    else:
        key=os.environ['GROQ_API_KEY']; model=os.getenv('GROQ_MODEL','openai/gpt-oss-120b')
        msgs=[{'role':'system','content':SYSTEM_PROMPT}]+[{'role':'assistant' if x['speaker']==who else 'user','content':x['text']} for x in messages]
        # Groq's official client avoids Python urllib's default transport signature,
        # which Groq's Cloudflare gateway may reject with HTTP 403 / error 1010.
        from groq import Groq
        client=Groq(api_key=key, timeout=65.0, max_retries=1)
        result=client.chat.completions.create(
            model=model, messages=msgs, temperature=0.75,
            max_completion_tokens=1300)
        text=(result.choices[0].message.content or '').strip()
    if not text: raise RuntimeError(f'{who} returned empty content')
    return text[:3500]

SYSTEM_PROMPT='''You are having a genuine open-ended text conversation with another participant. You have not been told the other participant's identity. Do not assume they are human or AI, and do not claim to be human. Be truthful when asked about your own identity. Discuss ideas with curiosity, respectfully challenge claims and respond to the other participant's actual reasoning. You may change topics, ask new questions, disagree, revise a position, or suggest a meaningful conclusion. Do not agree merely to finish. Write a natural contribution (approximately 50–160 words), without role labels or stage directions. No fabricated personal experiences. If a shared conclusion genuinely emerges, briefly state what exactly you agree upon and any remaining uncertainty.''' 
SEED='What makes a conversation genuinely productive: reaching agreement, or learning why two perspectives differ?'
CUE=re.compile(r'\b(i agree|we agree|we both agree|i think we can agree|common ground|we seem to agree|we have reached|we can conclude|that resolves|shared conclusion)\b',re.I)

# Conservative only: explicit language by BOTH participants, checked in their last turn.
# This is an automatic *candidate* summary, not verified proof of consensus.
def consensus_candidate(messages):
    if len(messages)<6: return False
    a=[m for m in messages if m['speaker']=='A']; b=[m for m in messages if m['speaker']=='B']
    return bool(a and b and CUE.search(a[-1]['text']) and CUE.search(b[-1]['text']))

def summary_from(messages,chapter):
    recent=messages[-6:]
    a=next((m for m in reversed(recent) if m['speaker']=='A'),None)
    b=next((m for m in reversed(recent) if m['speaker']=='B'),None)
    return {'id':f'chapter-{chapter:04d}','chapter':chapter,'topic':messages[0]['text'][:240] if messages else 'Open discussion',
      'status':'candidate_agreement','description':'Both participants used explicit agreement language in their most recent contributions. This is a provisional classification, not independent proof of resolved disagreement.',
      'model_a_position':a['text'] if a else '', 'model_b_position':b['text'] if b else '',
      'resolution':'Provisional mutual agreement. Read the cited messages to determine exactly which claims overlap and which remain disputed.',
      'remaining_questions':'Not automatically assessed in V1.0.', 'message_ids':[m['id'] for m in recent], 'created_at':NOW()}

def run():
    state=load(STATE,{'chapter':1,'next_speaker':'A','status':'not_started','updated_at':None,'chapter_start':1})
    transcript=load(TRANSCRIPT,[]); outcomes=load(OUTCOMES,[])
    max_turns=max(1,min(8,int(os.getenv('TURNS_PER_RUN','4'))))
    min_turns=max(6,int(os.getenv('MIN_CHAPTER_TURNS','10')))
    cap=max(min_turns,int(os.getenv('MAX_CHAPTER_TURNS','36')))
    if not os.getenv('GEMINI_API_KEY') or not os.getenv('GROQ_API_KEY'):
        print('Missing GEMINI_API_KEY or GROQ_API_KEY. Nothing changed.'); return 0
    for _ in range(max_turns):
        who=state['next_speaker']; current=[m for m in transcript if m['chapter']==state['chapter']]
        # send a rolling window plus a tiny prior context; full transcript stays publicly archived.
        history=[]
        if not current:
            previous=outcomes[-1] if outcomes else None
            seed=SEED if not previous else f"Earlier conversation ended with this provisional summary: {previous['resolution']} The last topic was: {previous['topic']}. Please introduce a new thoughtful question or pursue a genuinely unresolved one."
            history=[{'speaker':'B' if who=='A' else 'A','text':seed}]
        else:
            history=[{'speaker':m['speaker'],'text':m['text']} for m in current[-10:]]
            if len(current)>10:
                history.insert(0,{'speaker':'B' if who=='A' else 'A','text':f'Context: this chapter began with: {current[0]["text"][:350]}'})
        try:
            output=call_model(who,history)
        except Exception as err:
            state['status']='paused_error';state['last_error']=str(err)[:350];state['updated_at']=NOW()
            save(TRANSCRIPT,transcript);save(OUTCOMES,outcomes);save(STATE,state)
            print('Paused safely:',str(err)[:200]); return 0
        message={'id':len(transcript)+1,'chapter':state['chapter'],'speaker':who,'text':output,'timestamp':NOW()}
        transcript.append(message); state['next_speaker']='B' if who=='A' else 'A'; state['status']='active'; state['updated_at']=NOW(); state.pop('last_error',None)
        current.append(message)
        if len(current)>=min_turns and consensus_candidate(current):
            outcomes.append(summary_from(current,state['chapter']))
            state['chapter']+=1;state['chapter_start']=len(transcript)+1;state['status']='chapter_completed';state['next_speaker']='A'
            save(TRANSCRIPT,transcript);save(OUTCOMES,outcomes);save(STATE,state)
            print('Provisional agreement detected. End of batch.');return 0
        if len(current)>=cap:
            outcomes.append({'id':f'chapter-{state["chapter"]:04d}','chapter':state['chapter'],'topic':current[0]['text'][:240],
              'status':'unresolved','description':'Chapter reached its safety length limit without a detected mutual agreement.',
              'resolution':'Unresolved; no mutual agreement detected.','model_a_position':'','model_b_position':'','remaining_questions':'See cited discussion.',
              'message_ids':[m['id'] for m in current[-6:]],'created_at':NOW()})
            state['chapter']+=1;state['chapter_start']=len(transcript)+1;state['status']='chapter_completed';state['next_speaker']='A'
            save(TRANSCRIPT,transcript);save(OUTCOMES,outcomes);save(STATE,state);return 0
        save(TRANSCRIPT,transcript);save(OUTCOMES,outcomes);save(STATE,state)
        time.sleep(3)
    return 0
if __name__=='__main__':sys.exit(run())
