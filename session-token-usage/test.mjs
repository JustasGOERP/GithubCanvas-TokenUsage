import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const html = readFileSync(new URL("./ui.html", import.meta.url), "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/)[1].replace(/;load\(\);\s*$/, ";");
const elements = new Map();
const document = {
    getElementById(id) {
        if (!elements.has(id)) elements.set(id, { checked: false, value: "", innerHTML: "", querySelectorAll: () => [] });
        return elements.get(id);
    },
};
const context = vm.createContext({ document, location: { pathname: "/" }, assert, console });
vm.runInContext(script + `
const ym=new Date().toISOString().slice(0,7);
const model=(model,nano,calls=1,costCalls=calls)=>({model,ym,calls,cost_calls:costCalls,nano_aiu:nano,input:100,output:10,cache_read:0,reasoning:0});
const sess=(id,parent,models)=>({id,parent,title:id,project:"Project",folder:"folder",archived:false,input:100,output:10,cached:0,reasoning:0,months:{[ym]:{input:100,output:10,cached:0,reasoning:0}},models});
D={sessions:[sess("parent",null,[model("a",2e11)]),sess("child","parent",[model("b",1e11,2,1)]),sess("unknown",null,[])]};
index();
let tree=subtree(byId.get("parent"));
assert.equal(tree.cost,3);
assert.equal(tree.missingCost,1);
assert.equal(costText(tree),"≥ $3.00");
assert.equal(costText(byId.get("unknown")),"Unavailable");
let g=gather();
assert.equal(g.t.cost,3);
assert.equal(g.proj.get("Project").cost,3);
assert.equal(g.models.get("a").cost,2);
assert.equal(g.models.get("b").missingCost,1);
$("month").checked=true;index();
assert.equal(subtree(byId.get("parent")).cost,3);
const end=Date.UTC(2026,11,1),now=Date.UTC(2026,10,16);
quota={providerQuotas:[{provider:{kind:"copilot"},observationKind:"account_snapshot",quantityKind:"authoritative_budget",acquisitionStatus:"succeeded",unit:"ai_credits",quotaId:"premium_interactions",budgetMetadata:{unlimited:false,entitlement:20000,consumed:10000,resetAtEpochMs:end}}]};
let q=quotaSummary();assert.equal(q.limit,200);assert.equal(q.used,100);assert.equal(q.remaining,100);assert.equal(q.creditsLimit,20000);assert.equal(q.creditsUsed,10000);
let f=forecast(q,now);assert.equal(f.projected,200);assert.equal(f.difference,0);
assert.equal(forecast(q,end),null);
quota.providerQuotas[0].budgetMetadata.resetAtEpochMs=Date.UTC(new Date().getUTCFullYear(),new Date().getUTCMonth()+1,1);
summary();assert.match($("main").innerHTML,/Projected total[\\s\\S]*AI credits/);
quota.providerQuotas[0].acquisitionStatus="failed";assert.equal(quotaSummary(),null);
summary();
assert.match($("main").innerHTML,/Account balance unavailable/);
`, context);
console.log("Canvas billing calculations passed.");
