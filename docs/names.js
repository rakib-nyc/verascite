/* Case-name comparison, generated from verascite/names.py. Do not hand-edit.
 *
 * This is the check that catches the second-largest defect class: a real
 * citation attached to the wrong case. "Roe v. Wade, 500 F.3d 210" resolves to
 * a real record -- United States v. Corley -- and the mismatch is the finding.
 *
 * The rule that matters, and the reason a naive comparison is dangerous: if
 * everything two names share is caption boilerplate ("State", "People",
 * "Com."), they agree on nothing that identifies a case. But a shared
 * distinctive party, exact or misspelled, means they are the same case and must
 * never be flagged. Ported deliberately rather than reimplemented, so the
 * browser and the command line reach the same verdict.
 */
window.VERASCITE_NAMES = (function () {
  var NOISE = new Set(["a","aka","al","an","and","appellant","appellee","behalf","dba","defendant","estate","et","etal","ex","fka","her","his","in","individually","its","matter","of","on","parte","petitioner","plaintiff","re","rel","respondent","the","their","v","versus","vs"]);
  var GENERIC = new Set(["administrator","agency","attorney","authority","board","borough","bureau","city","cmwlth","com","comm","commission","commissioner","committee","commonwealth","commw","county","cwlth","department","dept","director","district","division","doe","general","government","juvenile","minor","office","people","people's","republic","roe","secretary","service","services","sheriff","st","state","states","town","township","united","unknown","us","usa","village","warden"]);
  var CANON = {"and":"s","s":"s","acad":"acad","academy":"acad","acads":"acad","admr":"admr","administrator":"admr","admrs":"admr","admx":"admx","administrix":"admx","admxs":"admx","admin":"admin","administrative":"admin","administration":"admin","admins":"admin","adver":"adver","advertising":"advert","advers":"adver","advert":"advert","adverts":"advert","agric":"agric","agriculture":"agric","agricultural":"agric","agrics":"agric","all":"all","alliance":"all","alls":"all","alt":"alt","alternative":"alt","alts":"alt","am":"am","america":"am","ams":"am","assn":"assn","association":"assoc","assns":"assn","assoc":"assoc","associate":"assoc","assocs":"assoc","atl":"atl","atlantic":"atl","atls":"atl","auth":"auth","authority":"auth","auths":"auth","auto":"auto","automobile":"auto","automotive":"auto","autos":"auto","ave":"ave","avenue":"ave","aves":"ave","bankr":"bankr","bankruptcy":"bankr","bankrs":"bankr","bd":"bd","board":"bd","bds":"bd","bhd":"bhd","brotherhood":"bhd","bhds":"bhd","bldg":"bldg","building":"bldg","bldgs":"bldg","broad":"broad","broadcast":"broad","broadcasting":"broad","broads":"broad","bros":"bros","brothers":"bros","bus":"bus","business":"bus","cas":"cas","casualty":"cas","cent":"cent","central":"cent","cents":"cent","chem":"chem","chemical":"chem","chems":"chem","cmty":"cmty","community":"cmty","cmtys":"cmty","cnty":"cnty","county":"cty","cntys":"cnty","co":"co","company":"co","cos":"co","coal":"coal","coalition":"coal","coals":"coal","coll":"coll","college":"coll","colls":"coll","commn":"comm","commission":"comm","commns":"commn","commr":"comm","commissioner":"comm","commrs":"commr","comm":"comm","committee":"comm","comms":"comm","commcn":"commcn","communication":"commcn","commcns":"commcn","comp":"comp","compensation":"comp","comps":"comp","comput":"comput","computer":"comput","computs":"comput","condo":"condo","condominium":"condo","condos":"condo","cong":"cong","congress":"cong","congressional":"cong","congs":"cong","consol":"consol","consolidated":"consol","consols":"consol","constr":"constr","construction":"constr","constrs":"constr","contl":"contl","continental":"contl","contls":"contl","coop":"coop","cooperative":"coop","coops":"coop","corp":"corp","corporation":"corp","corps":"corp","corr":"corr","corrections":"corr","correctional":"corr","corrs":"corr","ctr":"ctr","center":"ctr","centre":"ctr","ctrs":"ctr","cty":"cty","ctys":"cty","def":"def","defense":"def","defs":"def","dept":"dept","department":"dept","depts":"dept","det":"det","detention":"det","dets":"det","dev":"dev","development":"dev","devs":"dev","dig":"dig","digital":"dig","digs":"dig","dir":"dir","director":"dir","dirs":"dir","disc":"disc","discount":"disc","discs":"disc","dist":"dist","district":"dist","dists":"dist","distrib":"distrib","distributor":"distrib","distributing":"distrib","distribs":"distrib","div":"div","division":"div","divs":"div","e":"e","east":"e","eastern":"e","es":"e","econ":"econ","economic":"econ","economics":"econ","economical":"econ","economy":"econ","econs":"econ","educ":"educ","education":"educ","educational":"educ","educs":"educ","elec":"elec","electric":"elec","electrical":"elec","electricity":"elec","electronic":"elec","elecs":"elec","empr":"empr","employer":"empr","emprs":"empr","empt":"empt","employment":"empt","empts":"empt","emp":"emp","employee":"emp","emps":"emp","engg":"engg","engineering":"engg","enggs":"engg","engr":"engr","engineer":"engr","engrs":"engr","enter":"enter","enterprise":"enter","enters":"enter","entmt":"entmt","entertainment":"entmt","entmts":"entmt","envt":"envt","environment":"envt","envts":"envt","envtl":"envtl","environmental":"envtl","envtls":"envtl","equal":"equal","equality":"equal","equals":"equal","equip":"equip","equipment":"equip","equips":"equip","exr":"exr","executor":"exr","exrs":"exr","exx":"exx","executrix":"exx","exxs":"exx","examr":"examr","examiner":"examr","examrs":"examr","exch":"exch","exchange":"exch","exchs":"exch","exec":"exec","executive":"exec","execs":"exec","exp":"exp","exporter":"exp","exportation":"exp","exps":"exp","expl":"expl","exploration":"expl","exploratory":"expl","expls":"expl","fedn":"fedn","federation":"fedn","fedns":"fedn","fed":"fed","federal":"fed","feds":"fed","fid":"fid","fidelity":"fid","fids":"fid","fin":"fin","finance":"fin","financial":"fin","financing":"fin","fins":"fin","found":"found","foundation":"found","founds":"found","gen":"gen","general":"gen","gens":"gen","gend":"gend","gender":"gend","gends":"gend","glob":"glob","global":"glob","globs":"glob","govt":"govt","government":"govt","govts":"govt","grp":"grp","group":"grp","grps":"grp","guar":"guar","guaranty":"guar","guars":"guar","hosp":"hosp","hospital":"hosp","hosps":"hosp","hous":"hous","housing":"hous","imp":"imp","importer":"imp","importation":"imp","imps":"imp","inc":"inc","incorporated":"inc","incs":"inc","indem":"indem","indemnity":"indem","indems":"indem","indep":"indep","independent":"indep","indeps":"indep","indus":"indus","industry":"indus","industries":"indus","industrial":"indus","info":"info","information":"info","infos":"info","ins":"ins","insurance":"ins","inst":"inst","institute":"inst","institution":"inst","insts":"inst","intl":"intl","international":"intl","intls":"intl","inv":"inv","investment":"inv","invs":"inv","invr":"invr","investor":"invr","invrs":"invr","lab":"lab","laboratory":"lab","labs":"lab","liab":"liab","liability":"liab","liabs":"liab","litig":"litig","litigation":"litig","litigs":"litig","ltd":"ltd","limited":"ltd","ltds":"ltd","mach":"mach","machine":"mach","machinery":"mach","machs":"mach","maint":"maint","maintenance":"maint","maints":"maint","mar":"mar","maritime":"mar","mars":"mar","mech":"mech","mechanic":"mech","mechanical":"mech","mechs":"mech","med":"med","medical":"med","medicine":"med","meds":"med","meml":"meml","memorial":"meml","memls":"meml","merch":"merch","merchant":"merch","merchandise":"merch","merchandising":"merch","merchs":"merch","metro":"metro","metropolitan":"metro","metros":"metro","mfg":"mfr","manufacturing":"mfr","mfgs":"mfg","mfr":"mfr","manufacturer":"mfr","mfrs":"mfr","mgmt":"mgmt","management":"mgmt","mgmts":"mgmt","mkt":"mkt","market":"mkt","mkts":"mkt","mktg":"mktg","marketing":"mktg","mktgs":"mktg","mortg":"mortg","mortgage":"mortg","mortgs":"mortg","mun":"mun","municipal":"mun","muns":"mun","mut":"mut","mutual":"mut","muts":"mut","n":"n","north":"n","northern":"n","ns":"n","nat":"nat","natural":"nat","nats":"nat","natl":"natl","national":"natl","natls":"natl","ne":"ne","northeast":"ne","northeastern":"ne","nes":"ne","no":"no","number":"no","nos":"no","nw":"nw","northwest":"nw","northwestern":"nw","nws":"nw","op":"op","opinion":"op","ops":"op","org":"org","organization":"org","organizing":"org","orgs":"org","pship":"pship","partnership":"pship","pships":"pship","pac":"pac","pacific":"pac","pacs":"pac","par":"par","parish":"par","pars":"par","pers":"pers","personal":"pers","personnel":"pers","pharm":"pharm","pharmaceutics":"pharm","pharmaceutical":"pharm","pharmaceuticals":"pharm","pharms":"pharm","pres":"pres","preserve":"pres","preservation":"pres","prob":"prob","probation":"prob","probs":"prob","prod":"prod","product":"prod","production":"prod","prods":"prod","profl":"profl","professional":"profl","profls":"profl","prop":"prop","property":"prop","props":"prop","prot":"prot","protection":"prot","prots":"prot","pub":"pub","public":"pub","pubs":"pub","publg":"publg","publishing":"publg","publgs":"publg","publn":"publn","publication":"publn","publns":"publn","rr":"rr","railroad":"rr","rrs":"rr","rd":"rd","road":"rd","rds":"rd","ref":"ref","refining":"ref","refs":"ref","regl":"regl","regional":"regl","regls":"regl","rehab":"rehab","rehabilitation":"rehab","rehabs":"rehab","reprod":"reprod","reproduction":"reprod","reproductive":"reprod","reprods":"reprod","res":"res","resource":"res","resources":"res","rest":"rest","restaurant":"rest","rests":"rest","ret":"ret","retirement":"ret","rets":"ret","ry":"ry","railway":"ry","rys":"ry","sholder":"sholder","shareholder":"sholder","sholders":"sholder","south":"s","southern":"s","ss":"ss","steamship":"ss","steamships":"ss","sss":"ss","sav":"sav","savings":"sav","savs":"sav","sch":"sch","school":"sch","schools":"sch","schs":"sch","sci":"sci","science":"sci","scis":"sci","se":"se","southeast":"se","southeastern":"se","ses":"se","secy":"secy","secretary":"secy","secys":"secy","sec":"sec","security":"sec","securities":"sec","secs":"sec","serv":"serv","service":"serv","servs":"serv","socy":"socy","society":"socy","socys":"socy","soc":"soc","social":"soc","socs":"soc","sol":"sol","solution":"sol","sols":"sol","st":"st","street":"st","sts":"st","subcomm":"subcomm","subcommittee":"subcomm","subcomms":"subcomm","sur":"sur","surety":"sur","surs":"sur","sw":"sw","southwest":"sw","southwestern":"sw","sws":"sw","sys":"sys","system":"sys","systems":"sys","tech":"tech","technology":"tech","techs":"tech","tel":"tel","telephone":"tel","telegraph":"tel","tels":"tel","telecomm":"telecomm","telecommunication":"telecomm","telecomms":"telecomm","temp":"temp","temporary":"temp","temps":"temp","tpk":"tpk","turnpike":"tpk","tpks":"tpk","tr":"tr","trustee":"tr","trs":"tr","transcon":"transcon","transcontinental":"transcon","transcons":"transcon","transp":"transp","transport":"transp","transportation":"transp","transps":"transp","twp":"twp","township":"twp","twps":"twp","us":"us","unitedstates":"us","uss":"us","unif":"unif","uniform":"unif","unifs":"unif","univ":"univ","university":"univ","univs":"univ","util":"util","utility":"util","utils":"util","vill":"vill","village":"vill","vills":"vill","w":"w","west":"w","western":"w","ws":"w","ala":"ala","alabama":"ala","alaska":"alaska","ariz":"ariz","arizona":"ariz","ark":"ark","arkansas":"ark","cal":"cal","california":"cal","colo":"colo","colorado":"colo","conn":"conn","connecticut":"conn","del":"del","delaware":"del","fla":"fla","florida":"fla","ga":"ga","georgia":"ga","haw":"haw","hawaii":"haw","idaho":"idaho","ill":"ill","illinois":"ill","ind":"ind","indiana":"ind","iowa":"iowa","kan":"kan","kansas":"kan","ky":"ky","kentucky":"ky","la":"la","louisiana":"la","me":"me","maine":"me","md":"md","maryland":"md","mass":"mass","massachusetts":"mass","mich":"mich","michigan":"mich","minn":"minn","minnesota":"minn","miss":"miss","mississippi":"miss","mo":"mo","missouri":"mo","mont":"mont","montana":"mont","neb":"neb","nebraska":"neb","nev":"nev","nevada":"nev","nh":"nh","newhampshire":"nh","nj":"nj","newjersey":"nj","nm":"nm","newmexico":"nm","ny":"ny","newyork":"ny","nc":"nc","northcarolina":"nc","nd":"nd","northdakota":"nd","ohio":"ohio","okla":"okla","oklahoma":"okla","or":"or","oregon":"or","pa":"pa","pennsylvania":"pa","ri":"ri","rhodeisland":"ri","sc":"sc","southcarolina":"sc","sd":"sd","southdakota":"sd","tenn":"tenn","tennessee":"tenn","tex":"tex","texas":"tex","utah":"utah","vt":"vt","vermont":"vt","va":"va","virginia":"va","wash":"wash","washington":"wash","wva":"wva","westvirginia":"wva","wis":"wis","wisconsin":"wis","wyo":"wyo","wyoming":"wyo","associates":"assoc","regulatory":"reg","reg":"reg","regulation":"reg","regulations":"reg","usa":"us","unitedstatesofamerica":"us","manufacturers":"mfr","services":"serv"};
  var PASS = 0.6;
  var REVIEW = 0.34;
  var GENERIC_CEILING = 0.3;
  var FUZZY_MIN_LEN = 5;
  var SPELLING_RATIO = 0.82;

  function tokens(name) {
    if (!name) { return []; }
    var words = String(name).toLowerCase()
      .replace(/[^a-z0-9\s]/g, " ").split(/\s+/).filter(Boolean);
    var out = [];
    words.forEach(function (w) {
      var c = CANON[w] || w;
      if (NOISE.has(c) || c.length < 2) { return; }
      if (out.indexOf(c) < 0) { out.push(c); }
    });
    return out;
  }

  function ratio(a, b) {
    // Longest-common-subsequence similarity; close enough to the Python
    // matcher's purpose here, which is only to forgive a misspelling.
    var m = a.length, n = b.length;
    if (!m || !n) { return 0; }
    var prev = new Array(n + 1).fill(0), cur;
    for (var i = 1; i <= m; i++) {
      cur = new Array(n + 1).fill(0);
      for (var j = 1; j <= n; j++) {
        cur[j] = a[i - 1] === b[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], cur[j - 1]);
      }
      prev = cur;
    }
    return (2 * prev[n]) / (m + n);
  }

  function sameToken(x, y) {
    if (x === y) { return true; }
    if (x.length < FUZZY_MIN_LEN || y.length < FUZZY_MIN_LEN) { return false; }
    if (Math.abs(x.length - y.length) > 2) { return false; }
    return ratio(x, y) >= SPELLING_RATIO;
  }

  function shared(a, b) {
    var out = [];
    a.forEach(function (x) {
      b.forEach(function (y) { if (sameToken(x, y) && out.indexOf(x) < 0) { out.push(x); } });
    });
    return out;
  }

  function distinctive(list) {
    return list.filter(function (t) { return !GENERIC.has(t); });
  }

  /* Returns "match" | "mismatch" | "unclear". Only "mismatch" is a finding, and
   * it is reached only when both names identify a specific party and the
   * parties are different. */
  function compare(claimed, actual) {
    var a = tokens(claimed), b = tokens(actual);
    if (!a.length || !b.length) { return "unclear"; }
    if (a.join(" ") === b.join(" ")) { return "match"; }

    var common = shared(a, b);
    if (distinctive(common).length) { return "match"; }

    var da = distinctive(a), db = distinctive(b);
    if (!da.length || !db.length) { return "unclear"; }
    // Both name a specific party and they share none of them.
    return "mismatch";
  }

  return { compare: compare, tokens: tokens, PASS: PASS, REVIEW: REVIEW,
           GENERIC_CEILING: GENERIC_CEILING };
})();
