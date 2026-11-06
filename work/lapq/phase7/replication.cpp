// Independent counted sorting replication; C++17, standard library only.
// Algorithmic ancestry: Benomar--Coester's paper and this project's independently
// engineered phase2/native_benchmark.cpp finger-search design. This file neither
// loads nor embeds upstream Python classes. Upstream is a small-case oracle only.
// Metric convention follows the released artifact: clean/dirty key comparisons,
// with head-sentinel comparisons free. Rank-index work, advice/order generation,
// and extracting the already sorted list are excluded from that metric.
// Both goRight and goLeft are evaluated before finger-search branch selection.
// JSONL input per case: {id,model,predictions:[rank by key],order:[permutation],
// heights:[positive height by insertion step],trace:false}. Values are unique
// ordinals0..n-1. Offline stable-sorts ordinal keys by predicted rank; online and
// dirty use the supplied order. Signed predictions, including negative, are valid.
// The optional model bh_analytic instead takes {id,model,n}; it is the author's
// analytic reference, not a measured heap implementation. No timing is reported.
// clang++ -std=c++17 -O3 -DNDEBUG replication.cpp -o replication

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <string>
#include <variant>
#include <vector>

static void require(bool ok, const std::string& message) {
    if (!ok) throw std::runtime_error(message);
}
static std::string quoted(const std::string& s) {
    std::ostringstream out; out << '"';
    for (unsigned char c : s) {
        if (c == '\\' || c == '"') out << '\\' << c;
        else if (c == '\n') out << "\\n";
        else if (c == '\r') out << "\\r";
        else if (c == '\t') out << "\\t";
        else if (c < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(c) << std::dec;
        else out << c;
    }
    return out.str() + '"';
}

// Small strict JSON reader, intentionally restricted to integer numeric inputs.
struct Json {
    using Array = std::vector<Json>;
    using Object = std::map<std::string, Json>;
    std::variant<std::nullptr_t, int64_t, bool, std::string, Array, Object> value;
    Json() : value(nullptr) {}
    template<class T> explicit Json(T v) : value(std::move(v)) {}
    const Object& object() const { return std::get<Object>(value); }
    const Array& array() const { return std::get<Array>(value); }
    int64_t number() const { return std::get<int64_t>(value); }
    const std::string& string() const { return std::get<std::string>(value); }
    bool boolean() const { return std::get<bool>(value); }
    const Json& at(const std::string& key) const { return object().at(key); }
    bool has(const std::string& key) const { return object().count(key) != 0; }
};
class Parser {
    const std::string& s; size_t pos = 0;
    void space() { while (pos < s.size() && (s[pos]==' ' || s[pos]=='\t' || s[pos]=='\r' || s[pos]=='\n')) ++pos; }
    char take() { require(pos < s.size(), "unexpected end of JSON"); return s[pos++]; }
    void literal(const std::string& text) { require(s.compare(pos,text.size(),text)==0,"invalid JSON literal"); pos+=text.size(); }
    std::string string() {
        require(take()=='"',"expected JSON string"); std::string result;
        while (true) {
            unsigned char c=take(); if(c=='"') break;
            require(c>=32,"control byte in JSON string");
            if(c=='\\') {
                c=take();
                if(c=='n') c='\n'; else if(c=='r') c='\r'; else if(c=='t') c='\t';
                else if(c=='b') c='\b'; else if(c=='f') c='\f';
                else require(c=='"'||c=='\\'||c=='/',"unsupported JSON escape (use ASCII case IDs)");
            }
            result += char(c);
        }
        return result;
    }
    Json parse(unsigned depth) {
        require(depth<20,"JSON nesting too deep"); space(); require(pos<s.size(),"missing JSON value");
        char c=s[pos];
        if(c=='"') return Json(string());
        if(c=='t') {literal("true");return Json(true);}
        if(c=='f') {literal("false");return Json(false);}
        if(c=='n') {literal("null");return Json();}
        if(c=='[') {
            ++pos; Json::Array a; space(); if(pos<s.size()&&s[pos]==']'){++pos;return Json(std::move(a));}
            while(true){a.push_back(parse(depth+1));space();char d=take();if(d==']')break;require(d==',',"expected array comma");}
            return Json(std::move(a));
        }
        if(c=='{') {
            ++pos; Json::Object o; space(); if(pos<s.size()&&s[pos]=='}'){++pos;return Json(std::move(o));}
            while(true){space();auto key=string();space();require(take()==':',"expected object colon");
                require(!o.count(key),"duplicate JSON field");o.emplace(std::move(key),parse(depth+1));
                space();char d=take();if(d=='}')break;require(d==',',"expected object comma");}
            return Json(std::move(o));
        }
        size_t start=pos;if(c=='-')++pos;
        require(pos<s.size()&&s[pos]>='0'&&s[pos]<='9',"expected integer");
        if(s[pos]=='0')++pos;else while(pos<s.size()&&s[pos]>='0'&&s[pos]<='9')++pos;
        std::string n=s.substr(start,pos-start);char* end=nullptr;errno=0;
        long long v=std::strtoll(n.c_str(),&end,10);
        require(errno!=ERANGE && *end=='\0',"integer out of range"); return Json(int64_t(v));
    }
public:
    explicit Parser(const std::string& input):s(input){}
    Json run(){Json v=parse(0);space();require(pos==s.size(),"trailing JSON input");return v;}
};
static std::vector<int64_t> integers(const Json& j) {
    std::vector<int64_t> out; out.reserve(j.array().size());
    for(const auto& v:j.array())out.push_back(v.number());return out;
}
template<class T> static void json_array(std::ostream& out,const std::vector<T>& values) {
    out << '[';for(size_t i=0;i<values.size();++i){if(i)out<<',';out<<values[i];}out<<']';
}

class CountedList {
    struct Node { int key=-1; unsigned height=0; std::vector<int> next,prev; };
    std::vector<Node> nodes;
    const std::vector<int64_t>& prediction;
    const std::vector<int64_t>& heights;
    size_t used=0;
    bool tracing;
    uint64_t digest=14695981039346656037ULL;
    int next(int node,unsigned level=0) const {
        require(node>=0,"null next access");const auto& n=nodes[size_t(node)];return level<n.height?n.next[level]:-1;
    }
    int prev(int node,unsigned level=0) const {
        require(node>=0,"null previous access");const auto& n=nodes[size_t(node)];return level<n.height?n.prev[level]:-1;
    }
    void hash_word(uint64_t word) {
        for(unsigned i=0;i<8;++i){digest^=(word&255);digest*=1099511628211ULL;word>>=8;}
    }
    int compare(int a,int b,bool dirty=false) {
        bool free=(a<0||b<0);int result;
        if(free) result=(a>b)-(a<b);
        else if(dirty){++dirty_count;result=(prediction[size_t(a)]>prediction[size_t(b)])-(prediction[size_t(a)]<prediction[size_t(b)]);}
        else{++clean_count;result=(a>b)-(a<b);}
        if(tracing){hash_word(dirty?1:0);hash_word(uint64_t(int64_t(a)));hash_word(uint64_t(int64_t(b)));hash_word(uint64_t(int64_t(result)));hash_word(free?1:0);}
        return result;
    }
public:
    uint64_t clean_count=0,dirty_count=0;
    CountedList(const std::vector<int64_t>& ranks,const std::vector<int64_t>& levels,bool trace)
        :nodes(ranks.size()+1),prediction(ranks),heights(levels),tracing(trace){
        nodes[0].key=-1;nodes[0].height=1;nodes[0].next={-1};nodes[0].prev={-1};
    }
    int head()const{return 0;}
    int node_for_key(int key)const{require(key>=0&&size_t(key+1)<nodes.size()&&nodes[size_t(key+1)].height>0,"missing key hint");return key+1;}
    int ordinary_predecessor(int key,bool dirty=false) {
        int current=0;
        // An extra empty head level in the Python loop performs no comparisons.
        for(int level=int(nodes[0].height)-1;level>=0;--level)
            while(next(current,unsigned(level))>=0 && compare(nodes[size_t(next(current,unsigned(level)))].key,key,dirty)<=0)
                current=next(current,unsigned(level));
        return current;
    }
    int finger_predecessor(int source,int key) {
        int successor=next(source);
        bool go_right=successor>=0 && compare(nodes[size_t(successor)].key,key)<0;
        bool go_left=compare(nodes[size_t(source)].key,key)>0;
        int current=source;
        if(go_right) {
            while(next(current,nodes[size_t(current)].height-1)>=0 &&
                  compare(nodes[size_t(next(current,nodes[size_t(current)].height-1))].key,key)<=0)
                current=next(current,nodes[size_t(current)].height-1);
            for(int level=int(nodes[size_t(current)].height)-1;level>=0;--level)
                while(next(current,unsigned(level))>=0 && compare(nodes[size_t(next(current,unsigned(level)))].key,key)<=0)
                    current=next(current,unsigned(level));
            return current;
        }
        if(go_left) {
            while(compare(nodes[size_t(prev(current,nodes[size_t(current)].height-1))].key,key)>=0)
                current=prev(current,nodes[size_t(current)].height-1);
            for(int level=int(nodes[size_t(current)].height)-1;level>=0;--level)
                while(compare(nodes[size_t(prev(current,unsigned(level)))].key,key)>=0)
                    current=prev(current,unsigned(level));
            return prev(current);
        }
        return current;
    }
    int insert_after(int key,int predecessor) {
        require(used<heights.size(),"height stream exhausted");
        int64_t drawn=heights[used++];require(drawn>0&&drawn<=1024,"height outside1..1024");
        int index=key+1;Node& node=nodes[size_t(index)];require(node.height==0,"duplicate ordinal insertion");
        node.key=key;node.height=unsigned(drawn);node.next.assign(node.height,-1);node.prev.assign(node.height,-1);
        if(node.height>nodes[0].height){nodes[0].next.resize(node.height,-1);nodes[0].prev.resize(node.height,-1);nodes[0].height=node.height;}
        for(unsigned level=0;level<node.height;++level){
            while(level>=nodes[size_t(predecessor)].height){predecessor=prev(predecessor,level-1);require(predecessor>=0,"invalid insertion ascent");}
            int successor=next(predecessor,level);node.prev[level]=predecessor;node.next[level]=successor;
            nodes[size_t(predecessor)].next[level]=index;if(successor>=0)nodes[size_t(successor)].prev[level]=index;
        }
        return index;
    }
    void validate()const{
        int current=next(0);size_t seen=0;
        while(current>=0){require(seen<prediction.size(),"level0 cycle");const auto& node=nodes[size_t(current)];
            require(node.key==int(seen),"output is not sorted ordinal permutation");
            for(unsigned level=0;level<node.height;++level){
                require(node.prev[level]>=0 && next(node.prev[level],level)==current,"nonreciprocal previous link");
                if(node.next[level]>=0)require(prev(node.next[level],level)==current && nodes[size_t(node.next[level])].key>node.key,"invalid forward link");
            }
            ++seen;current=node.next[0];
        }
        require(seen==prediction.size()&&used==prediction.size(),"insertion/height coverage mismatch");
    }
    uint64_t trace_digest()const{return digest;}
};

struct Result {
    std::string id,model;size_t n=0;uint64_t clean=0,dirty=0,digest=0;
    double analytic=0;bool reference=false,trace=false;
    std::vector<uint64_t> clean_by_insert,dirty_by_insert;
    std::vector<int> insertion_order;
};
static Result solve(const Json& input) {
    Result r;r.id=input.at("id").string();r.model=input.at("model").string();r.trace=input.has("trace")&&input.at("trace").boolean();
    if(r.model=="SL")r.model="offline";if(r.model=="OSL")r.model="online";if(r.model=="DC")r.model="dirty";if(r.model=="BH")r.model="bh_analytic";
    if(r.model=="bh_analytic"){
        int64_t n=input.at("n").number();require(n>0&&n<=10000000,"invalid analytic n");r.n=size_t(n);r.reference=true;
        for(size_t k=1;k<r.n;++k){unsigned depth=0;for(size_t v=k+1;v>1;v>>=1)++depth;r.analytic+=std::log2(double(depth))+double(depth)-1.0;}
        return r;
    }
    require(r.model=="offline"||r.model=="online"||r.model=="dirty","unknown model");
    auto ranks=integers(input.at("predictions")),heights=integers(input.at("heights"));r.n=ranks.size();
    require(r.n>0&&r.n<=10000000&&heights.size()==r.n,"invalid vector sizes");
    if(input.has("n"))require(input.at("n").number()==int64_t(r.n),"n differs from prediction size");
    r.insertion_order.resize(r.n);std::iota(r.insertion_order.begin(),r.insertion_order.end(),0);
    if(r.model=="offline")std::stable_sort(r.insertion_order.begin(),r.insertion_order.end(),[&](int a,int b){return ranks[size_t(a)]<ranks[size_t(b)];});
    else{
        auto order=integers(input.at("order"));require(order.size()==r.n,"order size differs");std::vector<bool> present(r.n,false);
        for(size_t i=0;i<r.n;++i){require(order[i]>=0&&size_t(order[i])<r.n&&!present[size_t(order[i])],"order is not a permutation");present[size_t(order[i])]=true;r.insertion_order[i]=int(order[i]);}
    }
    CountedList list(ranks,heights,r.trace);int finger=list.head();std::map<int64_t,int> rank_tail;
    for(size_t i=0;i<r.n;++i){int key=r.insertion_order[i],predecessor;
        if(r.model=="offline")predecessor=i==0?list.ordinary_predecessor(key):list.finger_predecessor(finger,key);
        else if(r.model=="online"){
            auto found=rank_tail.lower_bound(ranks[size_t(key)]);int hint=list.head();
            if(found!=rank_tail.begin()){--found;hint=list.node_for_key(found->second);}
            predecessor=list.finger_predecessor(hint,key);
        }else predecessor=list.finger_predecessor(list.ordinary_predecessor(key,true),key);
        finger=list.insert_after(key,predecessor);if(r.model=="online")rank_tail[ranks[size_t(key)]]=key;
        if(r.trace){r.clean_by_insert.push_back(list.clean_count);r.dirty_by_insert.push_back(list.dirty_count);}
    }
    list.validate();r.clean=list.clean_count;r.dirty=list.dirty_count;r.digest=list.trace_digest();return r;
}
static void output_json(std::ostream& out,const Result& r) {
    out<<std::setprecision(17)<<"{\"status\":\"ok\",\"id\":"<<quoted(r.id)<<",\"model\":"<<quoted(r.model)<<",\"n\":"<<r.n
       <<",\"metric\":\""<<(r.reference?"author_analytic_binary_heap_reference":"clean_key_comparisons_free_head")<<"\",\"clean_comparisons\":";
    if(r.reference)out<<r.analytic;else out<<r.clean;
    out<<",\"clean_comparisons_per_key\":"<<(r.reference?r.analytic:double(r.clean))/double(r.n)
       <<",\"dirty_comparisons\":"<<r.dirty<<",\"reference_only\":"<<(r.reference?"true":"false")
       <<",\"sorted_ok\":"<<(r.reference?"null":"true")<<",\"height_count\":"<<(r.reference?0:r.n);
    if(r.trace&&!r.reference){out<<",\"comparison_trace_fnv1a64\":"<<quoted(std::to_string(r.digest))<<",\"insertion_order\":";json_array(out,r.insertion_order);
        out<<",\"clean_by_insert\":";json_array(out,r.clean_by_insert);out<<",\"dirty_by_insert\":";json_array(out,r.dirty_by_insert);}
    out<<"}\n";
}
static std::string csv_quote(const std::string& s){std::string o="\"";for(char c:s){if(c=='"')o+='"';o+=c;}return o+'"';}
int main(int argc,char**argv){
    try{
        std::string input_path,output_path,format="jsonl";
        for(int i=1;i<argc;++i){std::string arg=argv[i];
            if(arg=="--help"){std::cout<<"replication [--input FILE] [--output FILE] [--format jsonl|csv]\nOne JSON input object per line; default stdin/stdout. No experiments are generated.\n";return 0;}
            require(i+1<argc,"missing option value");std::string value=argv[++i];
            if(arg=="--input")input_path=value;else if(arg=="--output")output_path=value;else if(arg=="--format")format=value;else throw std::runtime_error("unknown option");}
        require(format=="jsonl"||format=="csv","unknown output format");
        std::ifstream file_in;std::ofstream file_out;std::istream* in=&std::cin;std::ostream* out=&std::cout;
        if(!input_path.empty()){file_in.open(input_path);require(bool(file_in),"cannot open input");in=&file_in;}
        if(!output_path.empty()){file_out.open(output_path);require(bool(file_out),"cannot open output");out=&file_out;}
        if(format=="csv")*out<<"id,model,n,clean_comparisons,clean_comparisons_per_key,dirty_comparisons,reference_only,sorted_ok,height_count\n";
        std::string line;size_t number=0;bool failed=false;
        while(std::getline(*in,line)){++number;if(line.empty())continue;
            try{require(line.size()<256ULL*1024*1024,"oversize JSON line");Result r=solve(Parser(line).run());
                if(format=="jsonl")output_json(*out,r);
                else{*out<<std::setprecision(17)<<csv_quote(r.id)<<','<<r.model<<','<<r.n<<',';
                    if(r.reference)*out<<r.analytic;else *out<<r.clean;
                    *out<<','<<(r.reference?r.analytic:double(r.clean))/double(r.n)<<','<<r.dirty<<','<<(r.reference?"true":"false")<<','<<(r.reference?"":"true")<<','<<(r.reference?0:r.n)<<'\n';}
            }catch(const std::exception& e){failed=true;if(format=="jsonl")*out<<"{\"status\":\"error\",\"line\":"<<number<<",\"message\":"<<quoted(e.what())<<"}\n";else std::cerr<<"line"<<number<<": "<<e.what()<<'\n';}
            out->flush();
        }
        return failed?2:0;
    }catch(const std::exception& e){std::cerr<<"replication: "<<e.what()<<'\n';return 2;}
}
