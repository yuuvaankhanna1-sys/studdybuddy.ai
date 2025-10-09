
import streamlit as st
import os
import textwrap

# Optional OpenAI integration. If you want real AI, set OPENAI_API_KEY as an env var.
USE_OPENAI = bool(os.getenv("OPENAI_API_KEY"))

if USE_OPENAI:
    try:
        import openai
        openai.api_key = os.getenv("OPENAI_API_KEY")
    except Exception as e:
        USE_OPENAI = False

st.set_page_config(page_title="StuddyBuddy — Demo", layout="wide")

# ---------- Helpers ----------
def nav(page):
    st.session_state['page'] = page

def generate_summary_mock(text):
    # super-simple mock summary: take first 3 sentences or lines
    lines = [ln.strip() for ln in textwrap.wrap(text, width=200)]
    # break by sentence endings if possible
    import re
    sents = re.split(r'(?<=[.?!])\s+', text.strip())
    sents = [s for s in sents if s]
    summary = " ".join(sents[:3]) if len(sents) >= 1 else text[:300]
    # fallback: shorten
    if len(summary) > 500:
        summary = summary[:500] + "..."
    return summary

def generate_questions_mock(text, n=5):
    # Create naive questions by taking noun-ish phrases (split by commas) or sentences
    import re
    sents = re.split(r'(?<=[.?!])\s+', text.strip())
    sents = [s for s in sents if s]
    questions = []
    for i in range(n):
        src = sents[i % len(sents)] if sents else text[:120]
        # Make a question by replacing a word with a blank
        words = src.split()
        if len(words) > 4:
            idx = len(words)//2
            answer = words[idx].strip(',.')
            words[idx] = "_____"
            q = " ".join(words)
            questions.append({"q": f"{q} ?", "a": answer})
        else:
            questions.append({"q": f"What is: {src[:60]}...?", "a": src.split()[0] if src.split() else ""})
    return questions

def make_plan_mock(text, days=7):
    # Split text into chunks by words for each day
    words = text.split()
    chunk_size = max(50, len(words)//days + 1)
    plan = []
    for d in range(days):
        start = d*chunk_size
        chunk = " ".join(words[start:start+chunk_size])
        if not chunk:
            chunk = "Review previous notes and flashcards."
        plan.append(f"Day {d+1}: {chunk[:250]}...")
    return plan

def ai_generate(text, n_questions=5, days=7):
    if USE_OPENAI:
        # Example prompt - minimal. You can replace models/calls as desired.
        prompt = f"Summarize the following text into short bullet points, then create {n_questions} multiple-choice or short questions with answers, and then make a {days}-day revision plan. Keep answers concise.\n\nText:\n{text[:4000]}"
        try:
            resp = openai.ChatCompletion.create(
                model="gpt-4o-mini" if "gpt-4o-mini" in openai.Model.list() else "gpt-4o",
                messages=[{"role":"user","content":prompt}],
                max_tokens=600
            )
            output = resp.choices[0].message.content
            # Very naive parse: we just return the whole output as summary for now
            return {"summary": output, "questions": [], "plan": []}
        except Exception as e:
            st.warning(f"OpenAI call failed, falling back to mock generator. ({e})")
            return None
    else:
        return None

# ---------- Initialize session ----------
if 'page' not in st.session_state:
    st.session_state['page'] = 'Home'
if 'saved_packs' not in st.session_state:
    st.session_state['saved_packs'] = []

# ---------- Sidebar / Nav ----------
with st.sidebar:
    st.title("StuddyBuddy")
    st.write("AI-powered study packs — demo")
    if st.button("Home"): nav('Home')
    if st.button("Create Study Pack"): nav('Create')
    if st.button("Saved Packs"): nav('Saved')
    if st.button("Pricing"): nav('Pricing')
    if st.button("About"): nav('About')
    st.markdown("---")
    if st.button("Reset Demo Data"):
        st.session_state['saved_packs'] = []
        st.success("Demo data cleared.")

# ---------- Pages ----------
page = st.session_state['page']

if page == 'Home':
    st.header("StuddyBuddy — Demo")
    st.subheader("Paste notes → Get summary, questions, and a 7-day revision plan")
    col1, col2 = st.columns([2,1])
    with col1:
        st.markdown("""
        **Quick Demo Flow**
        1. Click *Create Study Pack*  
        2. Paste a chapter or some class notes  
        3. Hit *Generate* and see the summary, questions, and plan  
        4. Save packs to reuse later  
        """)
        if st.button("Try it now → Create Study Pack"):
            nav('Create')
    with col2:
        st.info("Tip: No OpenAI key needed for this demo — it uses a local mock generator.")

elif page == 'Create':
    st.header("Create a Study Pack")
    st.write("Paste your notes or textbook excerpt below (max ~20k chars).")
    text = st.text_area("Paste text here", height=250, placeholder="Paste chapter/notes...")

    # small options
    cols = st.columns(3)
    with cols[0]:
        board = st.selectbox("Board / Format", ["General", "CBSE", "ICSE", "IGCSE", "IB", "NEET/JEE"])
    with cols[1]:
        n_q = st.slider("Number of questions", 1, 10, 5)
    with cols[2]:
        days = st.selectbox("Revision plan length (days)", [3,5,7,14], index=2)

    if st.button("Generate Study Pack"):
        if not text.strip():
            st.error("Paste some text first.")
        else:
            with st.spinner("Generating..."):
                ai_out = ai_generate(text, n_questions=n_q, days=days)
                if ai_out is None:
                    summary = generate_summary_mock(text)
                    questions = generate_questions_mock(text, n_q)
                    plan = make_plan_mock(text, days)
                else:
                    # naive handling: put AI output into summary section
                    summary = ai_out.get("summary", "No summary parsed.")
                    questions = ai_out.get("questions", [])
                    plan = ai_out.get("plan", [])
            # display results
            st.subheader("Summary")
            st.write(summary)

            st.subheader("Questions")
            for i,q in enumerate(questions):
                st.write(f"**Q{i+1}.** {q.get('q')}")
                if 'a' in q:
                    with st.expander("Show answer"):
                        st.write(q.get('a'))

            st.subheader("7-Day Revision Plan")
            for p in plan:
                st.write(p)

            # Save option
            save_title = st.text_input("Save this pack as (title)", value="My Pack")
            if st.button("Save Pack"):
                pack = {"title": save_title, "summary": summary, "questions": questions, "plan": plan}
                st.session_state['saved_packs'].append(pack)
                st.success(f"Saved '{save_title}'")
                nav('Saved')

elif page == 'Saved':
    st.header("Saved Packs")
    packs = st.session_state.get('saved_packs', [])
    if not packs:
        st.info("No packs saved yet. Create one from 'Create Study Pack'.")
    else:
        for idx, pack in enumerate(packs):
            with st.expander(f"{idx+1}. {pack.get('title','Untitled')}"):
                st.markdown("**Summary**")
                st.write(pack.get('summary'))
                st.markdown("**Questions**")
                for i,q in enumerate(pack.get('questions',[])):
                    st.write(f"Q{i+1}. {q.get('q')}")
                st.markdown("**Plan**")
                for p in pack.get('plan',[]):
                    st.write(p)
                if st.button(f"Delete pack {idx+1}"):
                    st.session_state['saved_packs'].pop(idx)
                    st.experimental_rerun()

elif page == 'Pricing':
    st.header("Pricing (Demo)")
    st.write("This is a placeholder pricing page. For a real product you'd connect Stripe or Lemon Squeezy.")
    st.write("""
    **Free** — basic demo (this app)  
    **Pro** — unlimited packs, export, image upload (future)  
    **Ace** — team/school plans, analytics
    """)
    if st.button("Contact Sales"):
        st.write("Email: contact@studdybuddy.ai (demo)")

elif page == 'About':
    st.header("About StuddyBuddy (Demo)")
    st.write("This is a small demo app to show how StuddyBuddy's core flow can work. Use this as a base to build the real product.")
    st.write("How to run: see the README instructions.")
    st.markdown("---")
    st.write("Small dev notes:")
    st.write("- To enable real AI, set OPENAI_API_KEY in your environment before running.")
    st.write("- This demo uses in-session memory only (not persisted). For production use Supabase/Postgres.")
else:
    st.write("Unknown page. Resetting to Home.")
    nav('Home')
