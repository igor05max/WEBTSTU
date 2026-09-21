\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{jamt}[2026/09/21 JAMT master template 2026.6]
\LoadClass[11pt,a4paper,twoside]{article}
\RequirePackage{keyval}
% The generated profile is also the native Word formatting contract.
\newcommand{\JAMTGeometry}[1]{\PassOptionsToPackage{#1}{geometry}}
\newcommand{\JAMTDeclareRole}[8]{%
  \expandafter\def\csname JAMT@role@#1\endcsname{%
    \def\JAMT@size{#2}\def\JAMT@leading{#3}\def\JAMT@align{#4}%
    \def\JAMT@indent{#5}\def\JAMT@before{#6}\def\JAMT@after{#7}\def\JAMT@emphasis{#8}}}
\InputIfFileExists{jamt-profile.tex}{}{\ClassError{jamt}{Missing jamt-profile.tex}{Keep the complete template archive together.}}
\RequirePackage{fontspec,polyglossia}
\setdefaultlanguage{english}
\setotherlanguage{russian}
\IfFontExistsTF{\JAMTFont}{
  \setmainfont{\JAMTFont}\newfontfamily\cyrillicfont{\JAMTFont}
}{\setmainfont{Liberation Serif}\newfontfamily\cyrillicfont{Liberation Serif}}
\RequirePackage{amsmath,unicode-math}
\setmathfont{texgyretermes-math.otf}[Path=fonts/]
\RequirePackage{geometry}
\RequirePackage{microtype,multicol,needspace,graphicx,adjustbox,array,booktabs,ragged2e,xcolor,fancyhdr,xurl,hyperref}
\RequirePackage{longtable}
\setlength{\heavyrulewidth}{\JAMTTableRule}
\setlength{\lightrulewidth}{\JAMTTableRule}
\newcommand{\jamtmark}[1]{{\setlength{\fboxsep}{.15pt}\colorbox{yellow}{\strut #1}}}
\ExplSyntaxOn
\NewDocumentCommand{\JAMTMissing}{m}{
  \group_begin:
  \seq_set_split:Nnn \l_tmpa_seq { ~ } { #1 }
  \seq_map_indexed_inline:Nn \l_tmpa_seq {
    \int_compare:nNnT { ##1 } > { 1 } { \space }
    \jamtmark{##2}
  }
  \group_end:
}
\ExplSyntaxOff
\hypersetup{hidelinks,pdfcreator={JAMT master template},pdfproducer={XeLaTeX}}
\setlength{\columnsep}{\JAMTColumnGap}
\setlength{\parindent}{\JAMTBodyIndent}
\setlength{\parskip}{0pt}
\setlength{\multicolsep}{6pt}
\raggedcolumns
\setcounter{collectmore}{-1}
% multicol otherwise accepts a final balanced column up to 12 pt too tall,
% which can put the last line over the footer rule. Reflow such a column.
\setlength{\maxbalancingoverflow}{0pt}
% multicol can log an overfull trial before rejecting it and shipping a
% correctly split page. Trace the disposition, without changing pagination.
% Only warnings enclosed in a confirmed discarded trial may be excluded.
\RequirePackage{etoolbox}
\pretocmd{\balance@columns}{\typeout{JAMT-BALANCE-BEGIN}}{}{\ClassError{jamt-reference}{Cannot trace column balancing}{}}
\apptocmd{\balance@columns}{\iftoo@bad\typeout{JAMT-BALANCE-DISCARDED}\else\typeout{JAMT-BALANCE-KEPT}\fi}{}{\ClassError{jamt-reference}{Cannot trace column balancing}{}}
\setlength{\emergencystretch}{1.5em}
\tolerance=1400
\hbadness=2500
\widowpenalty=10000
\clubpenalty=10000
\raggedbottom
\pagestyle{fancy}
\fancyhf{}
\newcommand{\jamtjournal}{Journal of Advanced Materials and Technologies.}
\newcommand{\jamtauthors}{}
\fancyhead[LE,RO]{\fontsize{10}{11.5}\selectfont\bfseries\itshape\jamtjournal}
\fancyfoot[C]{\fontsize{10}{11.5}\selectfont\itshape\jamtauthors}
\fancyfoot[LE,RO]{\fontsize{9}{10}\selectfont\bfseries\thepage}
\renewcommand{\headrulewidth}{1.8pt}
\renewcommand{\footrulewidth}{1.8pt}
\renewcommand{\headrule}{\hrule width\headwidth height .4pt\vskip .8pt\hrule width\headwidth height 1.8pt}
\renewcommand{\footrule}{\hrule width\headwidth height 1.8pt\vskip .8pt\hrule width\headwidth height .4pt\vskip 3pt}
\newlength{\labtablewidth}
\newbox\jamtobjectbox
\newcommand{\labtablesize}{\JAMTTableSize}
\newcommand{\labtableleading}{11.5}
\newcommand{\labfigscale}{1.0}
\newcommand{\labimage}[2]{\adjustbox{max width=\labfigscale\linewidth,max height=0.85\textheight}{\includegraphics[width=#1pt]{#2}}}
\newcommand{\jamtlicense}[2]{%
  \par\noindent\begin{minipage}{\linewidth}\hrule\vspace{6pt}%
  \begin{minipage}[c]{.17\linewidth}\centering #1\end{minipage}\hfill%
  \begin{minipage}[c]{.82\linewidth}\fontsize{9}{11}\selectfont\justifying\noindent #2\end{minipage}%
  \par\vspace{6pt}\hrule\end{minipage}\par}
% Public semantic API: content is independent of font and page geometry.
\define@key{JAMTparagraph}{language}{\def\JAMT@language{#1}}
\define@key{JAMTparagraph}{before}{\def\JAMT@before{#1}}
\define@key{JAMTparagraph}{after}{\def\JAMT@after{#1}}
\define@key{JAMTparagraph}{leading}{\def\JAMT@leading{#1}}
\define@key{JAMTparagraph}{indent}{\def\JAMT@indent{#1}}
\newif\ifJAMT@keepnext
\define@key{JAMTparagraph}{keepnext}{\csname JAMT@keepnext#1\endcsname}
\newcommand{\JAMTParagraph}[3][]{%
  \par\begingroup
  \@ifundefined{JAMT@role@#2}{\ClassError{jamt}{Unknown paragraph role #2}{See TEMPLATE.md.}}{\csname JAMT@role@#2\endcsname}%
  \def\JAMT@language{english}\JAMT@keepnextfalse\setkeys{JAMTparagraph}{#1}%
  \ifdim\JAMT@before pt>0pt\addvspace{\JAMT@before pt}\fi
  {\selectlanguage{\JAMT@language}\fontsize{\JAMT@size}{\JAMT@leading}\selectfont
   \JAMT@align\setlength{\parindent}{\JAMT@indent pt}\JAMT@emphasis
   \ifdim\JAMT@indent pt=0pt\noindent\fi #3\par}%
  % Protect both sides of the heading's spacing. A penalty added only after
  % the glue still permits a column break immediately before that glue.
  \ifJAMT@keepnext\nobreak\fi
  \ifdim\JAMT@after pt>0pt\addvspace{\JAMT@after pt}\fi
  \ifJAMT@keepnext\nobreak\fi
  \endgroup}
\newcommand{\JAMTHeading}[2][heading_1]{\needspace{4\baselineskip}\JAMTParagraph[keepnext=true]{#1}{#2}}
\newcommand{\JAMTFrontStart}{\vspace*{\JAMTFrontOffset}}
% Short identifiers share a baseline. A long field wraps at full width,
% retaining DOI's right alignment instead of colliding with the UDC.
\newsavebox{\JAMT@identifierleft}
\newsavebox{\JAMT@identifierright}
\newcommand{\JAMTIdentifierLine}[2]{%
  \sbox{\JAMT@identifierleft}{#1}\sbox{\JAMT@identifierright}{#2}%
  \ifdim\dimexpr\wd\JAMT@identifierleft+\wd\JAMT@identifierright+1em\relax>\linewidth
    {\RaggedRight\noindent #1\par}{\raggedleft\noindent #2\par}%
  \else
    \noindent\makebox[\linewidth][s]{\usebox{\JAMT@identifierleft}\hfill\usebox{\JAMT@identifierright}}\par
  \fi}
\newcommand{\JAMTIdentifiers}[2]{\JAMTParagraph{editorial_metadata}{\JAMTIdentifierLine{#1}{#2}}}
\newcommand{\JAMTFrontDivider}{\par\noindent{\color{gray}\rule{\linewidth}{.25pt}}\par\nobreak}
\newenvironment{JAMTBody}{\begin{multicols}{\JAMTColumns}}{\end{multicols}}
\newenvironment{JAMTObject}{\par\addvspace{6pt}\noindent\begin{minipage}{\linewidth}\centering}{\end{minipage}\par\addvspace{6pt}}
\AtBeginDocument{\fontsize{\JAMTBodySize}{\JAMTBodyLeading}\selectfont}
\endinput
